"""Offline self-test: detection, stats, odds, betting and settlement against a fake API.

    python tests/selftest.py

Runs the @section checks below in order and prints "ok: <name>" for each; a failure prints "FAIL: <name>" and stops.
"""
import json
import math
import os
import re
import sys
import tempfile
import time
import traceback
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

from fivestack.app import KNOWN_AGENTS  # noqa: E402
from fivestack.arcade import ARCADE_PRICE, ArcadeManager  # noqa: E402
from fivestack.bananas import CATALOG, BananaManager  # noqa: E402
from fivestack.bank import BankManager  # noqa: E402
from fivestack.bets import BetError, BetManager  # noqa: E402
from fivestack.config import load_bettor_names  # noqa: E402
from fivestack.db import DB  # noqa: E402
from fivestack.forecasts import build_forecasts  # noqa: E402
from fivestack.henrik import HenrikError  # noqa: E402
from fivestack.hunt import FIELD_H, FIELD_W, MARGIN, MIN_HOP, TARGET_R, HuntManager  # noqa: E402
from fivestack.insights import AGENT_ROLE, betting_report, build_insights, odds_accuracy  # noqa: E402
from fivestack.moments import game_facts  # noqa: E402
from fivestack.gamestate import COMPLETE, FORFEIT, NO_CONTEST, ending, full_game_rounds  # noqa: E402
from fivestack.odds import SCORE_SD_MAX, OddsEngine, fair_chance, partial_game, score_model  # noqa: E402
from fivestack.parlay import correlation, price  # noqa: E402
from fivestack.recap import build_recap  # noqa: E402
from fivestack.rewards import RewardManager, beat_share  # noqa: E402
from fivestack.stats import aggregate, build_stats, deviation, player_metrics  # noqa: E402
from fivestack.timeline import clutches_and_multikills, extract_timeline, spike_sites  # noqa: E402
from fivestack.tracker import MODES, Tracker, TrackerError, parse_details  # noqa: E402

MEMBERS = [f"P{i}#TAG" for i in range(1, 6)]
PUUIDS = {f"P{i}": f"puuid-{i}" for i in range(1, 6)}


def stored_item(mid, puuid, team="Blue", mode="Competitive", map_="Ascent",
                started="2026-09-20T18:00:00Z", red=9, blue=13, kills=18):
    return {
        "meta": {"id": mid, "map": {"id": "x", "name": map_}, "version": "v", "mode": mode,
                 "started_at": started, "season": {"id": "s", "short": "e11a2"}, "region": "na", "cluster": "c"},
        "stats": {"puuid": puuid, "team": team, "level": 100, "character": {"id": "a", "name": "Jett"},
                  "tier": 20, "score": kills * 250, "kills": kills, "deaths": 14, "assists": 5,
                  "shots": {"head": 10, "body": 30, "leg": 2}, "damage": {"made": kills * 150, "received": 2500}},
        "teams": {"red": red, "blue": blue},
    }


def v4_player(puuid, team, kills=16):
    return {"puuid": puuid, "name": puuid, "tag": "T", "team_id": team, "party_id": "party-1",
            "agent": {"name": "Sova"},
            "stats": {"score": kills * 250, "kills": kills, "deaths": 13, "assists": 6, "headshots": 8,
                      "bodyshots": 20, "legshots": 1, "damage": {"dealt": kills * 160, "received": 2400}},
            "tier": {"id": 20, "name": "Diamond 3"}}


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.ratelimit = {}
        self.details_calls = []
        # Each account's current Riot ID; the "roster" section renames one and adds a sixth player.
        self.names = {puuid: (name, "TAG") for name, puuid in PUUIDS.items()}
        self.names["puuid-6"] = ("P6", "TAG")

    def _account(self, puuid):
        name, tag = self.names[puuid]
        return {"puuid": puuid, "region": "na", "name": name, "tag": tag, "card": "c"}

    def account(self, name, tag):
        self.calls += 1
        for puuid, (n, t) in self.names.items():
            if (n.lower(), t.lower()) == (name.lower(), tag.lower()):
                return self._account(puuid)
        raise HenrikError(404, "Not found")

    def account_by_puuid(self, puuid):
        self.calls += 1
        if puuid not in self.names:
            raise HenrikError(404, "Not found")
        return self._account(puuid)

    def stored_matches(self, region, puuid, mode=None, size=None, page=None):
        self.calls += 1
        n = puuid[-1]
        items = [stored_item("m1", puuid, kills=15 + int(n))]                      # all five -> 5-stack
        items.append(stored_item("m2", puuid, team="Red" if n == "5" else "Blue",   # P5 on other team -> reject
                                 started="2026-09-21T18:00:00Z"))
        if n != "5":                                                               # hole for P5 -> verify via details
            items.append(stored_item("m3", puuid, map_="Bind", started="2026-09-22T18:00:00Z", red=13, blue=11))
        items.append(stored_item("m4", puuid, mode="Deathmatch", started="2026-09-23T18:00:00Z"))  # mode filtered
        if n in "123":                                                             # only 3 present -> reject
            items.append(stored_item("m5", puuid, started="2026-09-19T18:00:00Z"))
        items.append(stored_item("m6", puuid, started="2026-09-18T18:00:00Z", red=1, blue=2))  # remake -> skipped
        items.append(stored_item("m7", puuid, mode="Unrated", started="2026-09-17T18:00:00Z"))  # not Competitive -> skipped
        return {"status": 200, "results": {"total": len(items)}, "data": items}

    def match_details(self, region, match_id):
        self.calls += 1
        self.details_calls.append(match_id)
        if match_id == "m3":
            players = [v4_player(p, "Blue") for p in PUUIDS.values()]
            players += [{"puuid": f"enemy-{i}", "team_id": "Red", "party_id": f"e{i}", "agent": {"name": "Omen"}, "stats": {}} for i in range(5)]
            return {"metadata": {"match_id": "m3", "map": {"name": "Bind"}, "queue": {"id": "competitive", "name": "Competitive"},
                                 "started_at": "2026-09-22T18:00:00Z", "game_length_in_ms": 2400000,
                                 "season": {"short": "e11a2"}, "region": "na"},
                    "players": players,
                    "teams": [{"team_id": "Blue", "rounds": {"won": 11, "lost": 13}, "won": False},
                              {"team_id": "Red", "rounds": {"won": 13, "lost": 11}, "won": True}]}
        if match_id == "m1":
            return {"metadata": {"match_id": "m1", "map": {"name": "Ascent"}, "queue": {"id": "competitive", "name": "Competitive"},
                                 "started_at": "2026-09-20T18:00:00Z", "game_length_in_ms": 2000000,
                                 "season": {"short": "e11a2"}, "region": "na"},
                    "players": [v4_player(p, "Blue") for p in PUUIDS.values()],
                    "teams": [{"team_id": "Blue", "rounds": {"won": 13, "lost": 9}, "won": True}]}
        raise HenrikError(404, "Not found")


SECTIONS = []  # (name, check) in the order they run


def section(name):
    """Register a check. Checks run in definition order and share one `shared` namespace (database, bets placed so
    far, ...), because later ones build on earlier ones: bets placed in "bets" are settled in "settlement"."""
    def register(check):
        SECTIONS.append((name, check))
        return check
    return register


def setup():
    tmp = tempfile.mkdtemp()
    # The odds boost lands on a random pick and would move the prices the checks expect; the "house" section turns
    # it back on and checks it.
    real_boost, BetManager.apply_boost = BetManager.apply_boost, lambda self, board: board
    db = DB(os.path.join(tmp, "t.db"))
    cfg = {"region": "na", "members": MEMBERS, "modes": ["competitive", "unrated"], "fetch_match_details": True,
           "details_per_sync": 5, "house_edge": 0.05, "starting_balance": 1000}
    client = FakeClient()
    engine = OddsEngine(cfg)
    bets = BetManager(cfg, db, engine)

    def on_new(matches):
        out = []
        for m in matches:
            out += bets.settle_for_match(m, db.match_players(m["match_id"]))
        return out

    tracker = Tracker(cfg, db, client, on_new_matches=on_new)
    return types.SimpleNamespace(tmp=tmp, db=db, cfg=cfg, client=client, engine=engine, bets=bets, tracker=tracker,
                                 real_boost=real_boost)


@section("accounts")
def accounts(shared):
    bets, db = shared.bets, shared.db
    # Bettor accounts: register, wrong password, duplicate name, claim of a legacy name.
    bets.register("Tester", "secret1")
    db.create_bettor("Legacy", 900)  # pre-accounts row without a password
    assert bets.leaderboard()[0]["claimed"] is True
    for bad in (("Tester", "wrong"), ("Nobody", "secret1"), ("Legacy", "anything")):
        try:
            bets.authenticate(*bad)
            raise AssertionError(f"authenticate should fail for {bad}")
        except BetError:
            pass
    try:
        bets.register("tester", "other")
        raise AssertionError("duplicate name should be refused")
    except BetError:
        pass
    assert bets.register("Legacy", "claimed")["balance"] == 900
    assert bets.authenticate("legacy", "claimed")["name"] == "Legacy"
    bets.change_password("Tester", "secret1", "secret2")
    assert bets.authenticate("Tester", "secret2")
    try:
        bets.place("Ghost", "team:win", "win", 10, {})
        raise AssertionError("unknown bettor should not be able to bet")
    except BetError:
        pass


@section("no games yet")
def no_games_yet(shared):
    # No games yet -> odds not ready, bets refused.
    assert shared.engine.build(shared.db)["ready"] is False
    try:
        shared.bets.place("Tester", "team:win", "win", 10, {})
        raise AssertionError("bet should be refused before any game exists")
    except BetError:
        pass


@section("detection")
def detection(shared):
    client, db = shared.client, shared.db
    res = shared.first_sync = shared.tracker.sync(full=True)
    assert res["ok"], res
    assert res["new_matches"] == 2, res
    ids = {m["match_id"] for m in db.matches()}
    assert ids == {"m1", "m3"}, ids
    assert "m3" in client.details_calls
    m3 = db.match("m3")
    assert m3["result"] == "loss" and m3["party_verified"] == 1 and m3["source"] == "details", m3
    m1 = db.match("m1")
    assert m1["result"] == "win" and m1["details_fetched"] == 1 and m1["party_verified"] == 1, m1
    rejected = db.get_meta("rejected_matches")
    assert "m2" in rejected and "m5" in rejected and "m4" not in rejected, rejected
    assert "m6" in rejected and not db.has_match("m6")  # a 2-1 remake is no contest: never recorded
    assert len(db.members()) == 5
    # Only Competitive counts, whatever config.json says: the Unrated game every member played isn't a squad game
    # or a member line, and a database holding games from other modes drops them on purge.
    assert shared.tracker.modes == set(MODES) == {"competitive"} and shared.cfg["modes"] == ["competitive", "unrated"]
    assert res["skipped_mode"] == 2 and not db.has_match("m7"), res  # m4 (Deathmatch) and m7 (Unrated)
    assert not db.query("SELECT 1 FROM member_games WHERE mode != 'competitive'")
    db.insert_match({"match_id": "unrated-old", "map": "Bind", "mode": "unrated", "mode_label": "Unrated", "started_ts": 1,
                     "rounds_won": 13, "rounds_lost": 2, "result": "win", "team": "Blue"},
                    [{"puuid": "puuid-1", "kills": 20}])
    db.insert_member_games([{"match_id": "unrated-old", "puuid": "puuid-1", "map": "Bind", "mode": "unrated", "started_ts": 1,
                             "rounds_won": 13, "rounds_lost": 2, "result": "win", "kills": 20, "team": "Blue"}])
    assert db.purge_other_modes(MODES) == {"games": 1, "lines": 1} and db.purge_other_modes(MODES) == {"games": 0, "lines": 0}
    assert not db.has_match("unrated-old") and db.match_players("unrated-old") == []
    assert not db.query("SELECT 1 FROM member_games WHERE match_id='unrated-old'") and db.count_matches() == 2


@section("stats")
def stats(shared):
    db = shared.db
    st = build_stats(db)
    assert st["team"]["games"] == 2 and st["team"]["wins"] == 1 and st["team"]["losses"] == 1
    p1 = next(m for m in st["members"] if m["name"] == "P1")
    assert p1["overall"]["games"] == 2 and p1["by_map"][0]["games"] == 1
    assert p1["tier_name"] == "Diamond 3"
    # Highest and lowest game for each major stat (both games are complete here).
    p1_rows = [r for r in db.player_rows() if r["puuid"] == "puuid-1"]
    assert set(p1["range"]) == {"acs", "kd", "kills", "deaths", "assists", "adr", "hs_pct"}, p1["range"]
    assert p1["range"]["kills"]["high"]["value"] == max(r["kills"] for r in p1_rows)
    assert p1["range"]["kills"]["low"]["value"] == min(r["kills"] for r in p1_rows)
    assert {p1["range"]["acs"]["high"]["match_id"], p1["range"]["acs"]["low"]["match_id"]} <= {"m1", "m3"}
    # The trend chart's data: every complete game oldest first, each member's values lined up with it.
    tl = st["timeline"]
    assert [g["match_id"] for g in tl["games"]] == ["m1", "m3"] and set(tl["series"]) == set(PUUIDS.values())
    assert tl["series"]["puuid-1"]["kills"] == [next(r["kills"] for r in p1_rows if r["match_id"] == g) for g in ("m1", "m3")]

    # 5-stack vs. other games.
    # Every counted-mode line is kept (m1, m2, m3, m5; not the deathmatch m4); only m2 and m5 are baseline.
    assert db.count_member_games() == 5 + 5 + 4 + 3, db.count_member_games()
    base = db.baseline_rows()
    assert sorted((r["puuid"], r["match_id"]) for r in base) == sorted(
        [(f"puuid-{i}", "m2") for i in range(1, 6)] + [(f"puuid-{i}", "m5") for i in range(1, 4)]
    ), base
    dv = p1["deviation"]
    assert dv["stack_games"] == 2 and dv["usual_games"] == 2 and dv["enough"] is False, dv
    assert all(mt["verdict"] == "too_few" for mt in dv["metrics"]), dv
    p5 = next(m for m in st["members"] if m["name"] == "P5")
    assert next(mt for mt in p5["deviation"]["metrics"] if mt["key"] == "win_rate")["usual"] == 0.0  # P5 was on the losing team in m2

    def line(kills, deaths, i):
        return {"match_id": f"x{i}", "rounds_won": 13, "rounds_lost": 10, "result": "win" if i % 2 else "loss",
                "kills": kills + i % 3, "deaths": deaths + i % 2, "assists": 4, "score": (kills + i % 3) * 230,
                "damage_dealt": (kills + i % 3) * 140, "headshots": 5, "bodyshots": 20, "legshots": 1}
    d = deviation([line(22, 12, i) for i in range(10)], [line(14, 16, i) for i in range(30)])
    by_key = {mt["key"]: mt for mt in d["metrics"]}
    assert d["enough"] and by_key["kpr"]["verdict"] == "better" and by_key["kpr"]["diff"] > 0, by_key["kpr"]
    assert by_key["dpr"]["diff"] < 0 and by_key["dpr"]["verdict"] == "better", by_key["dpr"]  # fewer deaths is better
    assert by_key["hs_pct"]["verdict"] == "same" and by_key["win_rate"]["verdict"] == "same", by_key
    assert d["standout"] in ("acs", "kpr", "dpr", "adr", "kd"), d["standout"]
    assert deviation([line(22, 12, 0)], []) is None


@section("odds")
def odds(shared):
    engine, db = shared.engine, shared.db
    board = engine.build(db, {"map": "Ascent", "agents": {"puuid-1": "Jett"}})
    assert board["ready"]
    assert len(board["player_props"]) == 5 * 4, len(board["player_props"])  # kills, deaths, assists, HS% (no ACS or ADR props)
    assert not any(mk["stat"] in ("acs", "adr") for mk in board["player_props"]) and [d["key"] for d in board["stat_defs"]] == ["kills", "deaths", "assists", "hs_pct"]
    # No total rounds or rounds won / lost over-unders any more, and no moment markets yet: these games have no timelines.
    assert [mk["market_id"] for mk in board["team"]] == ["team:win", "team:ot", "team:margin", "team:score"]
    assert board["team"][0]["basis"]["recent"] == ["loss", "win"]  # the last results, newest first (m3 then m1)
    ot_market = board["team"][1]
    assert ot_market["selections"][1]["prob"] > ot_market["selections"][0]["prob"]  # no game went to OT: "No" favoured
    tops = {mk["market_id"]: mk for mk in board["top_markets"]}  # 6 "tops the scoreboard" markets, each with a counter
    assert len(tops) == 12 and sum(mk["direction"] == "low" for mk in tops.values()) == 6, sorted(tops)
    assert tops["top:kills"]["counter_id"] == "low:kills" and tops["low:kills"]["pair"] == "top:kills"
    assert tops["low:kills"]["label"] == "Bottom fragger" and tops["low:acs"]["label"] == "Lowest ACS"
    assert tops["top:acs_rel"]["label"] == "Popped off" and tops["low:acs_rel"]["label"] == "Got diff'd"
    favourite = lambda mid: tops[mid]["selections"][0]["key"]  # noqa: E731
    assert favourite("top:kills") == "puuid-5" and favourite("low:kills") == "puuid-1", (favourite("top:kills"), favourite("low:kills"))
    # Margin and exact score cover every result, so their fair chances add up to 1; being many-way markets, each pick
    # carries double the house edge.
    partial = [mk for mk in board["team"] if mk["type"] in ("team_score", "team_margin")]
    assert all(abs(x["prob"] - min(0.985, max(0.01, x["fair_prob"] * 1.1))) < 1e-3 for mk in partial for x in mk["selections"])
    assert all(abs(sum(x["fair_prob"] for x in mk["selections"]) - 1) < 2e-3 for mk in partial), [sum(x["fair_prob"] for x in mk["selections"]) for mk in partial]
    for mk in board["player_props"] + [mk for mk in board["team"] if mk not in partial]:
        s = sum(x["prob"] for x in mk["selections"])
        assert 1.03 < s < 1.07, (mk["market_id"], s)
    for mk in board["top_markets"]:
        s = sum(x["prob"] for x in mk["selections"])
        assert 1.0 < s < 1.25, (mk["market_id"], s)
    assert engine.build(db, {"map": "Ascent"})["top_markets"][0]["selections"] == board["top_markets"][0]["selections"] or True


@section("bets")
def placing_bets(shared):
    bets, db = shared.bets, shared.db
    bet = shared.bet = bets.place("Tester", "team:win", "win", 100, {})
    assert bet["status"] == "pending" and db.get_bettor("Tester")["balance"] == 900
    bet2 = shared.bet2 = bets.place("Tester", "ou:kills:puuid-1", "over", 50, {})
    assert bet2["line"] is not None
    try:
        bets.place("Tester", "team:win", "win", 5000, {})
        raise AssertionError("overdraft should fail")
    except BetError:
        pass
    b3 = bets.place("Tester", "team:ot", "yes", 10, {})
    for args in ({}, {"by": "Legacy"}, {"by": None}):
        try:
            bets.cancel(b3["id"], **args)
            raise AssertionError(f"cancel should be refused for {args}")
        except BetError:
            pass
    bets.cancel(b3["id"], by="tester")
    assert db.bet(b3["id"])["status"] == "cancelled" and db.get_bettor("Tester")["balance"] == 850
    b4 = bets.place("Tester", "team:ot", "no", 10, {})
    bets.cancel(b4["id"], admin=True)
    assert db.bet(b4["id"])["status"] == "cancelled" and db.get_bettor("Tester")["balance"] == 850


@section("parlays")
def parlays(shared):
    bets, db = shared.bets, shared.db
    bets.register("Parlay", "secret1")
    try:
        bets.place_parlay("Parlay", [{"market_id": "team:win", "selection": "win"}], 10, {})
        raise AssertionError("a parlay needs at least 2 legs")
    except BetError:
        pass
    try:
        bets.place_parlay("Parlay", [
            {"market_id": "team:win", "selection": "win"},
            {"market_id": "team:win", "selection": "loss"},
        ], 10, {})
        raise AssertionError("a parlay cannot repeat the same market")
    except BetError:
        pass
    parlay_win = shared.parlay_win = bets.place_parlay("Parlay", [
        {"market_id": "team:win", "selection": "win"},
        {"market_id": "ou:kills:puuid-1", "selection": "over"},
    ], 20, {})
    leg_odds = [l["odds_decimal"] for l in json.loads(parlay_win["context"])["legs"]]
    assert parlay_win["market_type"] == "parlay" and parlay_win["odds_decimal"] > max(leg_odds)
    shared.parlay_lose = bets.place_parlay("Parlay", [
        {"market_id": "team:win", "selection": "win"},
        {"market_id": "ou:kills:puuid-1", "selection": "under"},
    ], 20, {})
    assert db.get_bettor("Parlay")["balance"] == 960  # 1000 - 20 - 20
    # Two games of history is too few to judge correlation: the legs' odds are just multiplied.
    corr = json.loads(parlay_win["context"])["corr"]
    assert corr["factor"] == 1.0 and corr["odds_decimal"] == parlay_win["odds_decimal"] == corr["independent_decimal"], corr
    assert all(len(l["hist"]) == 2 for l in json.loads(parlay_win["context"])["legs"])  # replayed on both games

    # Team legs that decide each other (or can't both win) are refused, whatever the lines.
    refused = [([("team:score", "13-5"), ("team:win", "win")], "already decides"),
               ([("team:margin", "w6+"), ("team:win", "win")], "already decides"),
               ([("team:score", "13-5"), ("team:margin", "w6+")], "already decides"),
               ([("team:ot", "yes"), ("team:margin", "w6+")], "can't both win"),  # an overtime win counts as 1-2
               ([("team:win", "loss"), ("team:margin", "w1-2")], "can't both win"),
               ([("team:score", "13-5"), ("team:ot", "yes")], "can't both win")]
    for legs, why in refused:
        try:
            bets.quote_parlay([{"market_id": m, "selection": s} for m, s in legs], {})
            raise AssertionError(f"{legs} should be refused")
        except BetError as e:
            assert why in str(e), (legs, str(e))
    bets.quote_parlay([{"market_id": "team:win", "selection": "win"}, {"market_id": "team:ot", "selection": "yes"}], {})
    bets.quote_parlay([{"market_id": "team:win", "selection": "win"}, {"market_id": "ou:kills:puuid-1", "selection": "over"}], {})

    # Legs that won together more than chance are priced together. 30 games where two legs won the same 15: they
    # won together 15 times against 7.5 expected, a ratio of (15 + 3) / (7.5 + 3) with 3 pseudo-games of "no link".
    together, apart = "1" * 15 + "0" * 15, "10" * 15
    c = correlation([together, together])
    assert c["linked"] == [[0, 1]] and abs(c["factor"] - 18 / 10.5) < 1e-3 and c["games"] == 30, c
    assert correlation([together, "01" * 15])["factor"] == 1.0  # unrelated: no cut
    assert correlation([together, "0" * 15 + "1" * 15])["factor"] == 1.0  # never together: odds are never boosted
    assert correlation(["1" * 5 + "0" * 4, "1" * 5 + "0" * 4])["factor"] == 1.0  # 9 games: too few to judge
    assert correlation([together + "-" * 10, "-" * 10 + together])["games"] == 20  # voids leave a game out
    three = correlation([together, together, together, apart])  # a linked trio inside a longer parlay
    assert three["linked"] == [[0, 1, 2]] and three["factor"] > c["factor"], three
    assert price([2.0, 2.0], 18 / 10.5) == 2.33 and price([2.0, 2.0], 1.0) == 4.0
    assert price([1.5, 3.0], 4.0) == 3.0  # never less than the longest leg alone
    real_history = bets.leg_history
    bets.leg_history = lambda legs: [together] * len(legs)
    try:
        linked_parlay = bets.place_parlay("Parlay", [{"market_id": "team:win", "selection": "win"},
                                                     {"market_id": "ou:hs_pct:puuid-1", "selection": "over"}], 10, {})
    finally:
        bets.leg_history = real_history
    legs_odds = [l["odds_decimal"] for l in json.loads(linked_parlay["context"])["legs"]]
    assert linked_parlay["odds_decimal"] == price(legs_odds, 18 / 10.5) < round(legs_odds[0] * legs_odds[1], 2), linked_parlay
    bets.cancel(linked_parlay["id"], by="Parlay")
    # Counter ("bottom of the scoreboard") markets remember their direction, as singles and as parlay legs.
    bets.register("Counter", "secret2")
    low_single = bets.place("Counter", "low:kills", "puuid-1", 10, {})
    low_parlay = bets.place_parlay("Counter", [{"market_id": "low:acs_rel", "selection": "puuid-2"},
                                              {"market_id": "team:win", "selection": "win"}], 10, {})
    assert json.loads(low_single["context"])["direction"] == "low" and low_single["description"].startswith("Bottom fragger")
    assert json.loads(low_parlay["context"])["legs"][0]["meta"] == {"stat": "acs_rel", "direction": "low"}
    bets.cancel(low_single["id"], by="Counter")
    bets.cancel(low_parlay["id"], by="Counter")


@section("settlement")
def settlement(shared):
    bets, db = shared.bets, shared.db
    bet, bet2, parlay_win, parlay_lose = shared.bet, shared.bet2, shared.parlay_win, shared.parlay_lose
    # A new game arrives after the bets were placed -> settle.
    new_match = {"match_id": "m9", "map": "Haven", "mode": "competitive", "mode_label": "Competitive",
                 "started_at": "2026-09-25T18:00:00Z", "started_ts": time.time() + 5, "season": "e11a2",
                 "region": "na", "team": "Blue", "rounds_won": 13, "rounds_lost": 7, "result": "win",
                 "source": "stored", "party_verified": None, "details_fetched": 1}
    db.insert_match(new_match, [
        {"puuid": p, "agent": "Jett", "score": 5000, "kills": 30 if p == "puuid-1" else 10, "deaths": 10,
         "assists": 3, "headshots": 5, "bodyshots": 20, "legshots": 1, "damage_dealt": 3000, "damage_received": 1500}
        for p in PUUIDS.values()
    ])
    settled = bets.settle_for_match(db.match("m9"), db.match_players("m9"))
    assert len(settled) == 4, settled
    won_ids = {b["id"] for b in settled if b["status"] == "won"}
    lost_ids = {b["id"] for b in settled if b["status"] == "lost"}
    assert {bet["id"], bet2["id"], parlay_win["id"]} == won_ids and lost_ids == {parlay_lose["id"]}, settled
    bal = shared.tester_balance = db.get_bettor("Tester")["balance"]
    expected = 850 + 100 * bet["odds_decimal"] + 50 * bet2["odds_decimal"]
    assert abs(bal - expected) < 0.05, (bal, expected)
    parlay_bal = db.get_bettor("Parlay")["balance"]
    parlay_expected = 960 + 20 * parlay_win["odds_decimal"]  # no legs voided, so the pre-agreed price applies
    assert abs(parlay_bal - parlay_expected) < 0.05, (parlay_bal, parlay_expected)
    win_legs = json.loads(db.bet(parlay_win["id"])["context"])["legs"]
    assert [l["result"] for l in win_legs] == ["won", "won"], win_legs
    lose_legs = json.loads(db.bet(parlay_lose["id"])["context"])["legs"]
    assert [l["result"] for l in lose_legs] == ["won", "lost"], lose_legs
    lb = bets.leaderboard()
    tester_row = next(r for r in lb if r["name"] == "Tester")
    assert tester_row["won"] == 2 and tester_row["cancelled"] == 2, tester_row
    parlay_row = next(r for r in lb if r["name"] == "Parlay")
    assert parlay_row["won"] == 1 and parlay_row["lost"] == 1, parlay_row
    # The house's take from bets: stakes minus payouts on settled bets, and the estimate from each price and chance.
    house = shared.house = bets.house()
    done = [b for b in db.bets() if b["status"] in ("won", "lost")]
    assert house["bets"] == len(done) == 4 and house["staked"] == sum(b["stake"] for b in done) == 190
    assert abs(house["actual_take"] - (190 - sum(b["payout"] for b in done))) < 0.01, house

    def priced(b):  # the return per credit the bet was priced at: under 1 by the house edge
        ctx = json.loads(b["context"])
        legs = ctx["legs"] if b["market_type"] == "parlay" else [{**ctx, "odds_decimal": b["odds_decimal"]}]
        return math.prod(leg["odds_decimal"] * leg["fair_prob"] for leg in legs)
    assert abs(house["expected_take"] - sum(b["stake"] * (1 - priced(b)) for b in done)) < 0.01, house
    assert 0 < house["expected_take"] < 190 * 0.25, house


@section("scoreboard markets")
def scoreboard_markets(shared):
    bets, db = shared.bets, shared.db
    # Bottom-of-the-scoreboard and relative-ACS settlement.
    def top_bet(sel, direction, metrics, stat="kills"):
        ctx = {"stat": stat, **({"direction": direction} if direction else {})}
        return bets._evaluate({"market_type": "top", "selection": sel, "line": None, "context": json.dumps(ctx)},
                              {"mode": "competitive", "rounds_won": 13, "rounds_lost": 7}, metrics)
    spread = {"a": {"kills": 5}, "b": {"kills": 9}, "c": {"kills": 12}}
    assert top_bet("a", "low", spread)[0] == "won" and top_bet("c", "low", spread)[0] == "lost"
    assert top_bet("c", None, spread)[0] == "won"  # bets from before counter markets existed are "top" bets
    assert top_bet("a", "low", {"a": {"kills": 5}, "b": {"kills": 5}, "c": {"kills": 12}})[2].startswith("Tie at the bottom")
    # Popped off / got diff'd: m9 ACS over each player's own ACS in their earlier 5-stack games (m1 and m3).
    m9_start = db.match("m9")["started_ts"]
    rel = {p["puuid"]: player_metrics(p, 20) for p in db.match_players("m9")}
    bets._add_relative_acs(rel, m9_start)
    own = {pu: aggregate([r for r in db.player_rows() if r["puuid"] == pu and r["started_ts"] < m9_start])["acs"] for pu in rel}
    assert all(abs(rel[pu]["acs_rel"] - rel[pu]["acs"] / own[pu]) < 1e-9 for pu in rel)
    assert top_bet("puuid-1", "high", rel, "acs_rel")[0] == "won"  # lowest earlier average, same ACS tonight
    assert top_bet("puuid-5", "low", rel, "acs_rel")[0] == "won"  # highest earlier average
    rel["new-player"] = {"acs": 300.0, "acs_rel": None}
    assert top_bet("puuid-1", "high", rel, "acs_rel")[0] == "void"  # someone has nothing to compare against


@section("insights")
def insights(shared):
    db, bet, bet2 = shared.db, shared.bet, shared.bet2
    unroled = [a for a in KNOWN_AGENTS if a.lower() not in AGENT_ROLE]
    assert not unroled, f"add these agents to insights.ROLES: {unroled}"
    # Games: m1 (Ascent, W 13-9, all Jett), m3 (Bind, L 11-13, all Sova), m9 (Haven, W 13-7, all Jett), days apart.
    ins = build_insights(db)
    # Agent pool: P1 played Jett in m1 and m9, Sova in m3.
    ip1_agents = {a["agent"]: a for a in next(p for p in ins["players"] if p["puuid"] == "puuid-1")["agents"]}
    assert ip1_agents["Jett"]["games"] == 2 and ip1_agents["Sova"]["games"] == 1 and ip1_agents["Sova"]["role"] == "Initiator"
    # Betting report card: Tester won a player prop and a match-result single; Parlay went 1-1 on parlays.
    rep = betting_report(db)
    assert rep["by_type"]["Tester"]["ou"]["won"] == 1 and rep["by_type"]["Tester"]["team_win"]["won"] == 1, rep["by_type"]["Tester"]
    assert (rep["by_type"]["Parlay"]["parlay"]["won"], rep["by_type"]["Parlay"]["parlay"]["lost"]) == (1, 1)
    assert "Counter" not in rep["by_type"]  # only cancelled bets: nothing settled
    # Odds accuracy: m9 has 3 distinct picks. "5-stack wins" was bet as a single and in both parlays but counts
    # once; P1's kills over (single and parlay leg) won, the under (parlay leg) lost.
    acc = rep["accuracy"]
    assert (acc["picks"], acc["won"], acc["verdict"]) == (3, 2, None), acc  # too few picks for a verdict
    win_chance = json.loads(bet["context"])["fair_prob"]  # new bets save the model's own chance
    assert abs(win_chance - fair_chance(bet["odds_decimal"], "team_win", 0.05)) < 0.01  # and it matches the price
    assert abs(acc["expected"] - (win_chance + 1)) < 1e-3, acc  # the over and the under add up to 1
    assert [t["key"] for t in acc["by_type"]] == ["ou", "team_win"] and sum(b["picks"] for b in acc["bins"]) == 3
    # Older bets without a saved chance fall back to the price: 20 even-money picks that won half the time are in
    # line with the odds; 20 picks priced at 30% that won 15 times mean those odds were too generous.
    adb = DB(os.path.join(shared.tmp, "accuracy.db"))
    for i in range(40):
        chance = 0.5 if i < 20 else 0.3
        bid = adb.insert_bet({"bettor": "A", "market_id": "team:win", "market_type": "team_win", "description": "x",
                              "selection": "win", "selection_label": "x", "line": None,
                              "odds_decimal": round(1 / (chance * 1.05), 2), "stake": 1.0, "placed_ts": 0,
                              "context": "{}", "status": "won" if (i % 2 if i < 20 else i < 35) else "lost"})
        adb.execute("UPDATE bets SET settled_match_id=? WHERE id=?", (f"g{i}", bid))
    old = odds_accuracy(adb, 0.05)
    assert (old["picks"], old["won"], old["verdict"]) == (40, 25, "generous"), old
    assert [(b["picks"], b["won"]) for b in old["bins"]] == [(0, 0), (20, 15), (20, 10), (0, 0), (0, 0)], old["bins"]
    even = old["bins"][2]
    assert abs(even["chance"] - 0.5) < 0.01 and even["range"][0] < 0.5 < even["range"][1], even
    assert old["bins"][1]["range"][0] > 0.3  # won far more often than the 30% the odds gave
    assert old["by_type"][0]["verdict"] == "generous"
    assert [g["match_id"] for g in ins["games"]] == ["m1", "m3", "m9"], ins["games"]
    assert [g["margin"] for g in ins["games"]] == [4, -2, 6] and all(g["form"] is None for g in ins["games"])
    assert [g["session"] for g in ins["games"]] == [1, 2, 3] and ins["moments"]["sessions"] == 3
    mo = ins["moments"]
    assert (mo["close"]["wins"], mo["close"]["losses"]) == (0, 1) and (mo["blowout"]["wins"], mo["blowout"]["losses"]) == (1, 0), mo
    assert mo["after_win"]["games"] == 0 and mo["after_loss"]["games"] == 0  # momentum only counts within a night
    assert ins["session_games"][0]["games"] == 3 and ins["session_games"][0]["wins"] == 2
    assert [(c["key"], c["games"], c["wins"]) for c in ins["comps"]] == [("5D", 2, 2), ("5I", 1, 0)], ins["comps"]
    m9 = ins["games"][2]
    assert abs(sum(m9["damage_share"].values()) - 1) < 1e-9 and abs(m9["damage_share"]["puuid-2"] - 0.2) < 1e-9
    # The running share: the first game's is its own split, and the last one's is every game's damage pooled.
    first = ins["games"][0]
    assert all(abs(first["damage_cum"][pu] - v) < 1e-9 for pu, v in first["damage_share"].items()), first
    pooled = {}
    for g in ins["games"]:
        for p in db.match_players(g["match_id"]):
            pooled[p["puuid"]] = pooled.get(p["puuid"], 0) + (p.get("damage_dealt") or 0)
    assert abs(sum(m9["damage_cum"].values()) - 1) < 1e-9
    assert all(abs(m9["damage_cum"][pu] - v / sum(pooled.values())) < 1e-9 for pu, v in pooled.items()), m9["damage_cum"]
    ip1 = next(p for p in ins["players"] if p["puuid"] == "puuid-1")
    assert (ip1["games_win"], ip1["games_loss"]) == (2, 1) and ip1["acs_win"] > ip1["acs_loss"], ip1
    assert set(ip1["maps"]) == {"Ascent", "Bind", "Haven"} and abs(sum(ip1["aim"][k] for k in ("head_pct", "body_pct", "leg_pct")) - 1) < 1e-9
    assert "bankroll" not in ins  # the profit chart lives on the Bettors tab now
    ins["bankroll"] = betting_report(db)["bankroll"]
    assert len(ins["bankroll"]) == 2, ins["bankroll"]  # cancelled bets are left out; Tester and Parlay both settled
    roll = next(r for r in ins["bankroll"] if r["name"] == "Tester")
    assert len(roll["points"]) == 2, roll
    assert abs(roll["points"][-1]["profit"] - (100 * (bet["odds_decimal"] - 1) + 50 * (bet2["odds_decimal"] - 1))) < 0.05, roll


@section("timelines")
def timelines(shared):
    db = shared.db
    # Round timelines: a v4-shaped record -> compact timeline -> clutches, multi-kills and spike sites.
    us, them = [f"u{i}" for i in range(1, 6)], [f"e{i}" for i in range(1, 6)]
    kill = lambda r, t, a, b: {"round": r, "time_in_round_in_ms": t, "killer": {"puuid": a}, "victim": {"puuid": b}, "weapon": {"name": "Vandal"}}  # noqa: E731
    v4 = {"players": [{"puuid": p, "team_id": "Blue"} for p in us] + [{"puuid": p, "team_id": "Red"} for p in them],
          "rounds": [{"winning_team": "Blue", "plant": {"site": "A", "player": {"team": "Red"}}, "defuse": {"player": {"team": "Blue"}}},
                     {"winning_team": "Red", "plant": {"site": "B", "player": {"team": "Red"}}, "defuse": None}],
          "kills": [kill(0, 1000, "e4", "u2"), kill(0, 2000, "e4", "u3"), kill(0, 3000, "e5", "u4"), kill(0, 4000, "e5", "u5")]  # u1 alone vs 5
                   + [kill(0, 5000 + i, "u1", e) for i, e in enumerate(them)]  # ...and aces them
                   + [kill(1, 1000 + i, "u2", e) for i, e in enumerate(them[:3])]  # u2 3K
                   + [kill(1, 5000 + i, "e4", u) for i, u in enumerate(["u1", "u3", "u4", "u5"])]  # u2 alone vs e4, e5
                   + [kill(1, 9000, "e5", "u2")]}
    tl = extract_timeline(v4, set(us))
    assert tl["our_team"] == "Blue" and len(tl["rounds"]) == 2 and len(tl["kills"]) == 17 and tl["rounds"][0]["defused"]
    assert extract_timeline({"players": v4["players"]}, set(us)) is None  # no round data: nothing to keep
    per = clutches_and_multikills([tl], us)
    assert per["u1"]["clutch"][5] == [1, 1] and per["u1"]["k5"] == 1, per["u1"]  # 1v5 won, with an ace
    assert per["u2"]["clutch"][2] == [1, 0] and per["u2"]["k3"] == 1, per["u2"]  # 1v2 lost, 3K
    assert per["u3"]["rounds"] == 2 and sum(a for a, _ in per["u3"]["clutch"].values()) == 0
    sites = spike_sites({"Ascent": [tl]})["Ascent"]
    assert sites["sites"]["A"] == {"att_plants": 0, "att_wins": 0, "def_plants": 1, "def_wins": 1}  # enemy plant, we retook
    assert sites["sites"]["B"]["def_plants"] == 1 and sites["sites"]["B"]["def_wins"] == 0
    assert sites["attack_rounds"] == 0  # Red planted in rounds 1-2, so Red attacked the first half
    db.save_timeline("m1", extract_timeline(v4 | {"players": [{"puuid": p, "team_id": "Blue"} for p in PUUIDS.values()] + v4["players"][5:]},
                                            set(PUUIDS.values())))
    (row,) = db.timelines()
    assert row["match_id"] == "m1" and row["map"] == "Ascent" and row["data"]["our_team"] == "Blue"
    assert build_insights(db)["rounds"]["games"] == 1


@section("rewards")
def rewards(shared):
    cfg, db, bets = shared.cfg, shared.db, shared.bets
    # Game rewards: 250 per game + up to 250 for beating your own baseline.
    assert beat_share(250, [100, 150, 200, 250, 300, 350]) == 3.5 / 6 and beat_share(9, [1, 2]) == 1.0 and beat_share(5, []) is None
    db.set_meta("rewards_since", db.match("m9")["started_ts"] - 60)  # rewards switched on after m1 / m3 were played
    rm = RewardManager({**cfg, "members": [{"riot_id": "P1#TAG", "bettor": "Tester"}] + MEMBERS[1:]}, db)
    base_rows = [{"puuid": "x", "started_ts": 1, "rounds_won": 13, "rounds_lost": 7, "score": sc} for sc in (2000, 3000, 4000, 5000, 6000, 7000)]
    base_rows.append({"puuid": "x", "started_ts": 99, "rounds_won": 13, "rounds_lost": 7, "score": 1})  # after the game: ignored
    q = rm.quote({"started_ts": 10, "rounds_won": 13, "rounds_lost": 7}, {"puuid": "x", "score": 5000}, base_rows)
    assert q["acs"] == 250 and q["baseline_games"] == 6 and q["bonus"] == 145, q  # 250 * 3.5/6 = 145.8 -> nearest 5
    assert rm.pay_for_match(db.match("m1"), db.match_players("m1")) == []  # games from before rewards existed
    assert rm.pay_for_match(db.match("m3"), db.match_players("m3")) == []
    lb_before = {r["name"]: r for r in bets.leaderboard()}
    paid = rm.pay_for_match(db.match("m9"), db.match_players("m9"))
    by = {r["puuid"]: r for r in paid}
    assert len(paid) == 5 and all(r["base"] == 250 and 0 <= r["bonus"] <= 250 and r["bonus"] % 5 == 0 for r in paid), paid
    assert by["puuid-1"]["bettor"] == "Tester"  # config override
    p2 = db.get_bettor("P2")  # no account yet -> created unclaimed, named after the member
    assert p2 and not p2.get("password_hash") and p2["balance"] == 1000 + 250 + by["puuid-2"]["bonus"], p2
    assert by["puuid-2"]["beat_share"] is None and by["puuid-2"]["bonus"] == 125  # only 2 baseline games: neutral bonus
    assert rm.pay_for_match(db.match("m9"), db.match_players("m9")) == []  # never paid twice
    loss = {**db.match("m9"), "match_id": "m10", "started_ts": db.match("m9")["started_ts"] + 3600,
            "rounds_won": 9, "rounds_lost": 13, "result": "loss"}
    db.insert_match(loss, [{**p, "match_id": "m10"} for p in db.match_players("m9")])
    lost = rm.pay_for_match(db.match("m10"), db.match_players("m10"))  # a loss pays the same way
    assert len(lost) == 5 and all(r["base"] == 250 and 0 <= r["bonus"] <= 250 for r in lost), lost
    assert next(r for r in lost if r["puuid"] == "puuid-2")["bonus"] == 125
    # The bonus compares against earlier 5-stack games only: P2 has m1, m3 before m9 and m1, m3, m9 before m10
    # (their two non-5-stack games, m2 and m5, don't count).
    assert by["puuid-2"]["baseline_games"] == 2 and next(r for r in lost if r["puuid"] == "puuid-2")["baseline_games"] == 3
    lb_after = {r["name"]: r for r in bets.leaderboard()}
    assert lb_after["Tester"]["profit"] == lb_before["Tester"]["profit"]  # rewards are not betting profit
    tester_lost = next(r for r in lost if r["puuid"] == "puuid-1")["bonus"]
    assert lb_after["Tester"]["rewards"] == 250 + by["puuid-1"]["bonus"] + 250 + tester_lost and lb_after["P2"]["profit"] == 0
    assert lb_after["P2"]["rewards"] == 375 + 375 and len(db.rewards()) == 10
    # bettor_names.json maps a nickname to another account (any case); a config.json `bettor` still wins over it.
    named = RewardManager({**cfg, "members": [{"riot_id": "P1#TAG", "bettor": "Tester"}] + MEMBERS[1:]}, db,
                          {"p3": "Kikii", "P1": "Ignored"})
    assert named.bettor_for(db.member("puuid-3")) == "Kikii" and db.get_bettor("kikii")
    assert named.bettor_for(db.member("puuid-1")) == "Tester" and named.bettor_for(db.member("puuid-4")) == "P4"
    names = load_bettor_names()  # the committed mapping parses
    assert names.get("it") == "Kikii" and names.get("fat") == "fatty", names
    win_rm = RewardManager({**cfg, "win_reward": 100}, db)  # optional extra for wins
    assert win_rm.quote(db.match("m9"), db.match_players("m9")[0], [])["base"] == 350
    assert win_rm.quote(db.match("m10"), db.match_players("m10")[0], [])["base"] == 250


@section("bananas")
def bananas(shared):
    db, bets = shared.db, shared.bets
    # Who gets a member's bananas: their bettor account (here the nickname, as RewardManager.account_name would say).
    accounts = lambda: {m["puuid"]: m.get("nickname") or m["name"] for m in db.members()}  # noqa: E731
    db.set_meta("bananas_since", 1000)  # games from before this never pay (set to "now" the first time, like rewards)
    bm = shared.bananas = BananaManager({"banana_per_game": 2, "starting_bananas": 0}, db, bets, accounts=accounts)  # starters: checked below
    board = bets.leaderboard()
    balances = {b["name"]: b["balance"] for b in db.bettors()}
    # Earning: 2 bananas for every game a member played (each member_games line, squad game or not), paid once.
    added = bm.earn()
    assert added > 0 and bm.earn() == 0, added
    games = {accounts()[r["puuid"]].lower(): r["n"] for r in db.query(
        "SELECT puuid, COUNT(*) AS n FROM member_games WHERE started_ts >= 1000 GROUP BY puuid")}
    assert games and all(n >= 2 for n in games.values()), games  # squad games plus each member's other games
    totals = db.banana_totals()
    assert set(totals) == set(games), (totals.keys(), games.keys())  # only members earn: a bettor who only bets doesn't
    for k, n in games.items():
        t = totals[k]
        assert t["earned"] == 2 * n and t["season_games"] == n and t["wallet"] == t["earned"] and t["spent"] == 0, (k, t, n)
        assert float(t["wallet"]).is_integer()
    assert not db.query("SELECT 1 FROM banana_ledger WHERE reason IN ('bet_win', 'reward')")  # credits don't make bananas
    assert all(r["note"].startswith(("Ascent", "Bind")) for r in db.query("SELECT note FROM banana_ledger WHERE reason='game'"))
    # A game from before bananas_since never pays, however many times earn() runs.
    db.insert_member_games([{"match_id": "ancient", "puuid": "puuid-1", "map": "Split", "mode": "competitive", "started_ts": 5,
                             "rounds_won": 13, "rounds_lost": 9, "result": "win", "kills": 20, "team": "Blue"}])
    assert bm.earn() == 0 and db.banana_totals()["p1"]["earned"] == totals["p1"]["earned"]
    db.execute("DELETE FROM member_games WHERE match_id='ancient'")  # later sections count the lines
    # Whole numbers only: a fraction left from when bananas were paid per credit is rounded away once.
    db.execute("INSERT INTO banana_ledger(bettor, delta, reason, ref, note, created_ts) VALUES('P3', 12.4, 'bet_win', 'bet:old', '', ?)", (time.time(),))
    assert bm.earn() == 1 and float(db.banana_wallet("P3")).is_integer() and bm.wallet("P3") == totals["p3"]["wallet"] + 12
    assert db.query_one("SELECT reason FROM banana_ledger ORDER BY id DESC")["reason"] == "rounding" and bm.earn() == 0

    # Buying: bananas only. Give Tester a test grant so every case can be tried.
    db.execute("INSERT INTO banana_ledger(bettor, delta, reason, ref, note, created_ts) VALUES('Tester', 500, 'test', 'grant', '', ?)", (time.time(),))
    wallet = bm.wallet("Tester")
    r = bm.buy("Tester", "bd-banana")
    assert abs(r["wallet"] - (wallet - 40)) < 1e-9 and db.banana_equipped()["tester"] == {"badge": "bd-banana"}
    for bad, msg in [(("Tester", "bd-banana"), "already own"), (("Tester", "nope"), "isn't in the shop"),
                     (("Tester", "th-onkey"), "costs 1000")]:
        try:
            bm.buy(*bad)
        except BetError as e:
            assert msg in str(e), (bad, e)
        else:
            raise AssertionError(f"buy {bad} should fail")
    assert abs(bm.wallet("Tester") - (wallet - 40)) < 1e-9 and len(db.banana_items("Tester")) == 1  # failures cost nothing
    bm.buy("Tester", "bd-monkey")  # a second badge replaces the first on, both owned
    assert db.banana_equipped()["tester"]["badge"] == "bd-monkey" and len(db.banana_items("tester")) == 2
    assert bm.equip("Tester", "badge", "bd-banana") == {"badge": "bd-banana"}
    for args in [("Tester", "badge", "nc-peel"), ("Tester", "name_color", "nc-peel"), ("Tester", "hat", "")]:
        try:
            bm.equip(*args)
        except BetError:
            pass
        else:
            raise AssertionError(f"equip {args} should fail")
    assert bm.equip("Tester", "badge", "") == {} and bm.looks().get("tester", {"worn": {}})["worn"] == {}
    bm.equip("Tester", "badge", "bd-monkey")
    assert bm.looks()["tester"]["worn"]["badge"]["emoji"] == "🐒"

    # Monkey business: on someone else only, text where the item needs it, gone once a game starts after it.
    for args, msg in [(("Tester", "sc-peel", "Tester"), "somebody else"), (("Tester", "sc-peel", "Nobody"), "Pick who"),
                      (("Tester", "sc-note", "P2", ""), "Write something"), (("Tester", "sc-title", "P2", "x" * 25), "24 characters")]:
        try:
            bm.buy(*args)
        except BetError as e:
            assert msg in str(e), (args, e)
        else:
            raise AssertionError(f"buy {args} should fail")
    bm.buy("Tester", "sc-note", "p2", "  nice   clutch ")
    bm.buy("Tester", "sc-jinx", "P2")
    starts = [r["started_ts"] for r in db.query("SELECT started_ts FROM matches ORDER BY started_ts DESC")]
    db.execute("UPDATE banana_pranks SET created_ts = ?", (starts[0] + 60,))  # m10 "starts" in the future: buy after it
    looks = bm.looks()["p2"]["pranks"]
    assert {p["item"] for p in looks} == {"sc-note", "sc-jinx"} and next(p for p in looks if p["item"] == "sc-note")["text"] == "nice clutch"
    assert next(p for p in looks if p["item"] == "sc-jinx")["games_left"] == 3
    assert len(starts) >= 3, starts
    db.execute("UPDATE banana_pranks SET created_ts = ?", (starts[1] - 60,))  # two games since: the jinx still holds
    assert next(p for p in bm.looks()["p2"]["pranks"] if p["item"] == "sc-jinx")["games_left"] == 1
    db.execute("UPDATE banana_pranks SET created_ts = ?", (starts[2] - 60,))  # three games since: it wore off
    assert [p["item"] for p in bm.looks()["p2"]["pranks"]] == ["sc-note"]  # the note stays its 3 days
    # Pranks with text land on the target as written.
    db.execute("INSERT INTO banana_ledger(bettor, delta, reason, ref, note, created_ts) VALUES('P2', 200, 'test', 'grant-p2', '', ?)", (time.time(),))
    p2_wallet = bm.wallet("P2")
    bm.buy("P2", "sc-heckle", "Tester", "you whiffed")
    heckle = bm.looks()["tester"]["pranks"][0]
    assert heckle["kind"] == "heckle" and heckle["text"] == "you whiffed" and 1 <= heckle["games_left"] < 3  # m9 / m10 "start" later
    assert abs(bm.wallet("P2") - (p2_wallet - 30)) < 1e-9
    spent = 40 + 60 + 25 + 60
    t = db.banana_totals()["tester"]
    assert abs(t["spent"] - spent) < 1e-9 and abs(t["wallet"] - (t["earned"] + 500 - spent)) < 1e-9, t

    # Nothing above touched credits or the leaderboard.
    assert bets.leaderboard() == board and {b["name"]: b["balance"] for b in db.bettors()} == balances
    troop = bm.troop()
    tester = next(r for r in troop["troop"] if r["name"] == "Tester")
    assert troop["troop"][0]["name"] == "Tester" and tester["items"] == 2 and tester["collection"] == 100
    prof = bm.profile("tester")
    assert prof["name"] == "Tester" and len(prof["owned"]) == 2 and prof["pranks_sent"] and prof["betting"]["balance"] == balances["Tester"]
    assert any(p["item_id"] == "sc-note" and p["active"] for p in bm.profile("P2")["pranks"])
    # Starting bananas: every account gets 50 once a season, not counted as earned.
    wallets = {b["name"]: db.banana_wallet(b["name"]) for b in db.bettors()}
    earned = {k: v["earned"] for k, v in db.banana_totals().items()}
    starter = BananaManager({"banana_per_game": 2, "starting_bananas": 50}, db, bets, accounts=accounts)
    assert starter.earn() == len(wallets) and starter.earn() == 0
    assert all(abs(db.banana_wallet(n) - (w + 50)) < 1e-9 for n, w in wallets.items())
    assert all(abs(db.banana_totals()[k]["earned"] - e) < 1e-9 for k, e in earned.items())
    shared.starter = starter
    order = [(i["slot"], i["price"]) for i in CATALOG]  # the shop lists each slot cheapest first
    assert all(a[0] != b[0] or a[1] <= b[1] for a, b in zip(order, order[1:]))
    assert len({i["id"] for i in CATALOG}) == len(CATALOG) and bm.shop(db.get_bettor("Tester"))["me"]["owned"] == ["bd-banana", "bd-monkey"]


@section("arcade")
def arcade(shared):
    db, bets = shared.db, shared.bets
    am = ArcadeManager(db)
    board = bets.leaderboard()
    balances = {b["name"]: b["balance"] for b in db.bettors()}
    wallet = db.banana_wallet("Tester")
    # A play costs bananas up front and hands back a one-time token.
    play = am.start("Tester", "catch")
    assert abs(db.banana_wallet("Tester") - (wallet - ARCADE_PRICE)) < 1e-9 and play["token"]
    db.create_bettor("Broke", 1000)
    for args, msg in [(("Tester", "pinball"), "doesn't exist"), (("Broke", "catch"), "costs")]:
        try:
            am.start(*args)
        except BetError as e:
            assert msg in str(e), (args, e)
        else:
            raise AssertionError(f"start {args} should fail")
    # The score has to fit the play: the right bettor, once, and no more than the game allows for the time it ran.
    for args, msg in [(("P2", play["token"], 100), "isn't yours"), (("Tester", play["token"], 600), "doesn't add up"),
                      (("Tester", play["token"], -1), "doesn't add up"), (("Tester", "nope", 10), "isn't yours")]:
        try:
            am.finish(*args)
        except BetError as e:
            assert msg in str(e), (args, e)
        else:
            raise AssertionError(f"finish {args} should fail")
    r = am.finish("Tester", play["token"], 300)
    assert r["new_best"] and r["rank"] == 1 and r["champion"] and r["best_before"] is None
    try:
        am.finish("Tester", play["token"], 300)
    except BetError as e:
        assert "already" in str(e)
    else:
        raise AssertionError("a play can only be finished once")
    assert not am.finish("Tester", am.start("Tester", "catch")["token"], 200)["new_best"]
    r = am.finish("P2", am.start("P2", "catch")["token"], 450)
    assert r["champion"] and [(b["bettor"], b["score"]) for b in r["board"]] == [("P2", 450), ("Tester", 300)]  # one line each
    old = am.start("Tester", "dash")
    db.execute("UPDATE arcade_plays SET started_ts = started_ts - 7200 WHERE token=?", (old["token"],))
    try:
        am.finish("Tester", old["token"], 10)
    except BetError as e:
        assert "timed out" in str(e)
    else:
        raise AssertionError("an old play can't be finished")
    summary = am.summary(db.get_bettor("Tester"))
    assert [g["key"] for g in summary["games"]] == ["catch", "says", "dash", "lab"] and summary["me"]["plays"] == 3
    assert next(g for g in summary["games"] if g["key"] == "catch")["my_best"] == 300
    t = db.banana_totals()["tester"]
    assert abs(db.banana_wallet("Tester") - (wallet - 3 * ARCADE_PRICE)) < 1e-9 and t["spent"] >= 3 * ARCADE_PRICE
    # The only prize is the board: no bananas back, and credits never move.
    assert [r for r in bets.leaderboard() if r["name"] != "Broke"] == board  # "Broke" is new, with no bets
    assert {b["name"]: b["balance"] for b in db.bettors() if b["name"] != "Broke"} == balances


@section("scientist")
def scientist(shared):
    # From Onkey's lore: the scientist offers to buy Onkey from a bettor who's nearly broke. It can only be refused,
    # which earns the "Not For Sale" title for nothing, once; after that he stops asking.
    from fivestack.arcade import GAMES
    from fivestack.bananas import ITEMS, OFFER_BELOW, OFFER_ITEM, SOCIAL_ITEMS

    db, bets, bm = shared.db, shared.bets, shared.bananas
    bets.register("Broke", "secret1")
    assert not bm.offer_open("Broke", OFFER_BELOW) and bm.offer_open("Broke", OFFER_BELOW - 1)  # only under the line
    board, wallet = bets.leaderboard(), db.banana_wallet("Broke")
    item = bm.refuse_offer("Broke")
    assert item == {"id": OFFER_ITEM, "name": "Not For Sale"} and ITEMS[OFFER_ITEM]["slot"] == "title"
    owned = db.query("SELECT price FROM banana_items WHERE bettor=? AND item_id=?", ("Broke", OFFER_ITEM))
    assert [r["price"] for r in owned] == [0] and not bm.offer_open("Broke", 0)  # free, and he stops asking
    bm.refuse_offer("Broke")  # refusing again changes nothing
    assert len(db.query("SELECT 1 FROM banana_items WHERE bettor=? AND item_id=?", ("Broke", OFFER_ITEM))) == 1
    assert db.banana_wallet("Broke") == wallet and bets.leaderboard() == board  # no bananas or credits move
    # The rest of him in the shop and the arcade: the prank, the theme, the badges and the cabinet.
    assert SOCIAL_ITEMS["sc-scientist"]["look"]["ticket_cls"] == "watched" and ITEMS["th-lab"]["look"]["theme"] == "lab"
    assert "bd-labcoat" in ITEMS and GAMES["lab"]["name"] == "Lab Escape"
    # A plush Onkey is a real product: neither the site nor the lore may reference it (CLAUDE.md, "Onkey's lore").
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for folder in ("web", "fivestack", "docs"):
        for fname in sorted(os.listdir(os.path.join(root, folder))):
            if not fname.endswith((".js", ".css", ".html", ".py")) and fname != "onkey-lore.md":
                continue
            with open(os.path.join(root, folder, fname), encoding="utf-8") as f:
                text = f.read().lower()
            assert not any(word in text for word in ("the plush", "plush copy", "plush replica", "bd-plush") + (("plush",) if folder == "docs" else ())), fname
    # The login page shows him watching one visit in LOGIN_WATCHER_ODDS, and always has the slot for him.
    from fivestack import app as appmod
    assert "{watcher}" in appmod.LOGIN_PAGE and "scientist.png" in appmod.LOGIN_WATCHER and appmod.LOGIN_WATCHER_ODDS == 20


@section("slots")
def slots(shared):
    from concurrent.futures import ThreadPoolExecutor
    from itertools import product
    from unittest.mock import patch
    from fivestack.slots import (MACHINES, STAKES, SYMBOLS, WILD, SlotManager, chances, draw_reel, draw_spin, multiplier,
                                 outcomes, payouts, rtp, win_chance)

    db = DB(os.path.join(shared.tmp, "slots.db"))
    db.create_bettor("Spinner", 1000)
    db.create_bettor("Empty", 0)
    manager = SlotManager(db)
    bets = BetManager({"starting_balance": 1000}, db, shared.engine)
    m = MACHINES["jackpot"]
    # Without a Golden Onkey only the six triples pay; every line with one pays. The six regular lines return 95.00%
    # (a 5% house edge), a win about every 8 spins; the secret Golden Onkey's outcomes come on top (97.61% in all).
    assert len(m["lines"]) == len(m["triples"]) == len(m["show"]) == len(m["wild1"]) == len(m["wild2"]) == len(SYMBOLS)
    combos = list(product(range(len(SYMBOLS)), repeat=3))
    assert sum(multiplier(m, r) > 0 for r in combos if WILD not in r) == 6
    assert all(multiplier(m, r) > 0 for r in combos if WILD in r)
    assert abs(rtp(m) - 0.95) < 1e-12 and abs(rtp(m, secret=True) - 0.97051) < 1e-12
    assert abs(sum(chances(m)) - 124050 / 1000000) < 1e-12 and [round(1 / c) for c in chances(m)] == [250, 20, 25, 125, 50, 500, 20000]
    assert abs(win_chance(m) - 127682 / 1000000) < 1e-12
    # The Golden Onkey: spotted alone (no line) pays 2x the stake; filling in a line, one doubles it and two triple it,
    # never past the 100x cap; three of them are the 100x jackpot, the top prize.
    assert payouts(m, [WILD, 1, 2]) == [{"kind": "spotted", "count": 1, "mult": 2}]
    assert payouts(m, [2, WILD, 2]) == [{"kind": "line", "symbol": 2, "mult": 4, "wild": 1},
                                         {"kind": "wild", "count": 1, "factor": 2, "mult": 4, "capped": False}] and multiplier(m, [2, WILD, 2]) == 8
    assert payouts(m, [WILD, 3, WILD])[1] == {"kind": "wild", "count": 2, "factor": 3, "mult": 40, "capped": False}  # 20x, tripled
    assert payouts(m, [WILD, 5, WILD]) == [{"kind": "line", "symbol": 5, "mult": 80, "wild": 2},
                                            {"kind": "wild", "count": 2, "factor": 3, "mult": 20, "capped": True}] and multiplier(m, [WILD, 5, WILD]) == 100
    assert multiplier(m, [5, 5, WILD]) == multiplier(m, [0, WILD, WILD]) == 100 and multiplier(m, [0, 0, WILD]) == 80
    assert max(multiplier(m, r) for r in combos) == 100
    assert payouts(m, [WILD] * 3) == [{"kind": "line", "symbol": WILD, "mult": 100, "wild": 0}] and multiplier(m, [WILD] * 3) == 100
    assert payouts(m, [3, 3, 3]) == [{"kind": "line", "symbol": 3, "mult": 20, "wild": 0}] and payouts(m, [0, 1, 1]) == []
    # The exact return, outcome by outcome, matches every combination of reels each outcome covers.
    assert sum(n * multiplier(m, r) for kind, i, n in outcomes(m) for r in [{"line": [i] * 3, "wild1": [i, i, WILD],
               "wild2": [i, WILD, WILD], "spot1": [WILD, 0, 1]}[kind]]) == 970510
    # The bigger the payout, the rarer the line.
    by_payout = sorted(range(len(SYMBOLS)), key=lambda i: m["triples"][i])
    assert all(chances(m)[a] > chances(m)[b] for a, b in zip(by_payout, by_payout[1:]))
    # A reel is drawn in proportion to its weights.
    with patch("fivestack.slots.secrets.randbelow", side_effect=range(sum(m["show"]))):
        drawn = [draw_reel(m["show"]) for _ in range(sum(m["show"]))]
    assert [drawn.count(i) for i in range(len(SYMBOLS))] == m["show"]
    # The line is picked first, each triple over exactly its "lines" tickets of the 1,000,000: banana takes 0-3,999,
    # ..., the Golden Onkey, the secret 200x symbol, 124,000-124,049; then the Golden Onkey's other outcomes.
    tickets = [sum(m["lines"][:i]) for i in range(len(SYMBOLS) + 1)]
    for i in range(len(SYMBOLS)):
        for t in (tickets[i], tickets[i + 1] - 1):
            with patch("fivestack.slots.secrets.randbelow", return_value=t):
                assert draw_spin(m) == [i, i, i], (i, t)
    # Then the Golden Onkey's outcomes, each on random reels: a pair it finishes, a symbol with two of them, a lone one.
    first = sum(m["lines"])
    with patch("fivestack.slots.secrets.randbelow", side_effect=[first, 2]):
        assert draw_spin(m) == [0, 0, WILD]  # the first wild1 ticket: bananas
    with patch("fivestack.slots.secrets.randbelow", side_effect=[first + sum(m["wild1"]) + 4, 0]):
        assert draw_spin(m) == [2, WILD, WILD]  # wild2's fifth ticket: a bell
    spot = first + sum(m["wild1"]) + sum(m["wild2"])
    with patch("fivestack.slots.secrets.randbelow", side_effect=[spot, 1]), \
            patch("fivestack.slots.draw_reel", side_effect=[3, 3, 3, 4]):
        assert draw_spin(m) == [3, WILD, 4]  # the other two redrawn until they differ
    # A loss is shown from the display weights without the Golden Onkey (any one would pay), redrawn if they match.
    plain = [w if i != WILD else 0 for i, w in enumerate(m["show"])]
    with patch("fivestack.slots.secrets.randbelow", return_value=spot + m["spot1"]), \
            patch("fivestack.slots.draw_reel", side_effect=[1, 1, 1, 0, 1, 1]) as reel:
        assert draw_spin(m) == [0, 1, 1]
    assert all(c.args == (plain,) for c in reel.call_args_list)
    assert spot + m["spot1"] == 127682 and len(m["show"]) == len(SYMBOLS)
    assert [s["key"] for s in SYMBOLS if s.get("secret")] == ["golden"] and max(m["triples"]) == m["triples"][6] == 100
    assert STAKES == (5, 10, 25, 50, 100, 250, 500)
    for i, (machine, reels, mult, stake) in enumerate([
            (None, [0, 0, 0], 40, 10), ("jackpot", [1, 1, 1], 3, 10), ("jackpot", [6, 6, 6], 100, 500),
            ("jackpot", [0, 1, 2], 0, 250), ("jackpot", [5, 5, 5], 80, 10), ("jackpot", [1, 1, 2], 0, 10)]):
        before = db.get_bettor("Spinner")["balance"]
        with patch("fivestack.slots.draw_spin", return_value=reels):
            out = manager.spin("spinner", machine, stake, f"test-spin-{i:016d}")
        assert out["spin"]["reels"] == reels and out["spin"]["payout"] == stake * mult
        assert out["balance"] == before + stake * (mult - 1)
        # Same request after a lost response: return the original outcome without a charge or a re-roll.
        assert manager.spin("Spinner", machine, stake, f"test-spin-{i:016d}") == out
    assert [m["name"] for m in manager.summary()["machines"]] == ["Slots"]
    before = db.get_bettor("Spinner")["balance"]
    for machine, stake, request in [("classic", 10, "retired-reference"), ("missing", 10, "valid-reference-1"), ([], 10, "valid-reference-1"),
                                    ("jackpot", True, "valid-reference-1"), ("jackpot", "10", "valid-reference-1"),
                                    ("jackpot", float("nan"), "valid-reference-1"), ("jackpot", float("inf"), "valid-reference-1"),
                                    ("jackpot", -5, "valid-reference-1"), ("jackpot", 10.5, "valid-reference-1"),
                                    ("jackpot", 1000, "valid-reference-1"),
                                    ("jackpot", None, "valid-reference-1"), ("jackpot", 10, None),
                                    ("jackpot", 10, "bad"), ("jackpot", 25, "test-spin-0000000000000000")]:
        try:
            manager.spin("Spinner", machine, stake, request)
        except BetError:
            pass
        else:
            raise AssertionError((machine, stake, request))
    assert db.get_bettor("Spinner")["balance"] == before
    for name in ("Empty", "Missing"):
        try:
            manager.spin(name, "jackpot", 5, "valid-reference-1")
        except BetError:
            pass
        else:
            raise AssertionError("unfunded or missing bettor spun")
    # Concurrent retries settle exactly once; concurrent different requests cannot overdraw.
    with patch("fivestack.slots.draw_spin", return_value=[0, 0, 0]), ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: manager.spin("Spinner", "jackpot", 5, "concurrent-retry-1"), range(8)))
    assert len({r["spin"]["id"] for r in results}) == 1
    assert db.get_bettor("Spinner")["balance"] == before + 5 * (40 - 1)
    db.create_bettor("LastFive", 5)
    def attempt(i):
        try:
            return manager.spin("LastFive", "jackpot", 5, f"concurrent-spend-{i}")
        except BetError:
            return None
    with patch("fivestack.slots.draw_spin", return_value=[0, 1, 2]), ThreadPoolExecutor(4) as pool:
        assert sum(r is not None for r in pool.map(attempt, range(4))) == 1
    assert db.get_bettor("LastFive")["balance"] == 0
    # The ledger insert and credit movement roll back together if storage fails.
    db.execute("CREATE TRIGGER fail_slot BEFORE INSERT ON slot_spins BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    before = db.get_bettor("Spinner")["balance"]
    try:
        manager.spin("Spinner", "jackpot", 10, "rollback-reference")
    except Exception as e:
        assert "test failure" in str(e)
    else:
        raise AssertionError("expected storage failure")
    db.execute("DROP TRIGGER fail_slot")
    assert db.get_bettor("Spinner")["balance"] == before
    row = next(r for r in bets.leaderboard() if r["name"] == "Spinner")
    assert row["profit"] == 0 and row["roi"] is None and row["slots"] == row["casino"] == before - 1000
    assert db.banana_wallet("Spinner") == 0 and not db.bets()
    assert manager.summary()["me"] is None and manager.summary()["history"] == []
    summary = manager.summary(db.get_bettor("Spinner"))
    assert summary["me"]["spins"] == 7 and summary["me"]["net"] == before - 1000
    assert len(summary["history"]) == 7
    # Line stats: each triple hit (two bananas, a cherry, an Onkey, a Golden Onkey) out of the bettor's tracked spins.
    assert summary["lines"]["spins"] == 7 and summary["lines"]["hits"] == [2, 1, 0, 0, 0, 1, 1]
    # The house: 800 staked by everyone, 51,430 paid out (50,000 of it the Golden Onkey jackpot), and the 5% edge it
    # expected to keep, which leaves the jackpot out (every spin recorded the 95% return without it).
    house = summary["house"]
    assert house["spins"] == 8 and house["staked"] == 800 and house["paid"] == 51430 and house["actual_take"] == -50630
    assert house["secret_paid"] == 50000 and house["expected_take"] == round(800 * (1 - rtp(m)), 2) == 40.0
    # The house ledger has a row per spin (none for the retries or the failed one), matching slots' own take.
    from fivestack.house import HouseManager, casino_nets
    house_mgr = HouseManager(db)  # its backfill finds every spin already recorded
    ledger = house_mgr.summary()
    assert [g["game"] for g in ledger["games"]] == ["slots"]
    assert ledger["games"][0]["season"] == {"rounds": 8, "staked": 800, "take": house["actual_take"], "expected": house["expected_take"]}
    assert ledger["season_take"] == ledger["all_time_take"] == house["actual_take"]
    assert casino_nets(db)["spinner"] == {"total": before - 1000, "slots": before - 1000}
    # Season stats: 5 wins in 7 spins, the Golden Onkey the biggest, and the last spin (the concurrent banana) a win.
    assert summary["me"]["wins"] == 5 and summary["me"]["since_win"] == 0
    assert summary["me"]["best"]["payout"] == 50000 and summary["me"]["best"]["reels"] == [6, 6, 6]
    # The squad's biggest wins this season, biggest first.
    assert [w["payout"] for w in summary["big_wins"]] == [50000, 800, 400, 200, 30]
    assert {w["bettor"] for w in summary["big_wins"]} == {"Spinner"} and summary["big_wins"][0]["reels"] == [6, 6, 6]
    season = bets.reset()
    assert manager.summary(db.get_bettor("Spinner"))["me"]["spins"] == 0
    assert manager.summary()["big_wins"] == [] and manager.summary(db.get_bettor("Spinner"))["me"]["best"] is None
    assert manager.house() == house and manager.lines("Spinner") == summary["lines"]  # both span every season
    assert db.query_one("SELECT COUNT(*) AS n FROM slot_spins WHERE season_id=?", (season["id"],))["n"] == 8
    # Old retry keys survive a reset, and reopening an existing database preserves history.
    assert manager.spin("Spinner", "jackpot", 10, "test-spin-0000000000000000")["balance"] == 1000
    assert next(r for r in bets.leaderboard() if r["name"] == "Spinner")["slots"] == 0
    # The reset moved the house's rows to the old season; all time is unchanged.
    assert house_mgr.summary()["season_take"] == 0 and house_mgr.summary()["all_time_take"] == house["actual_take"]
    db.conn.close()
    reopened = DB(os.path.join(shared.tmp, "slots.db"))
    assert reopened.query_one("SELECT COUNT(*) AS n FROM slot_spins")["n"] == 8
    reopened.execute("INSERT INTO slot_spins(bettor,machine,stake,reels,multiplier,payout,created_ts,request_id) "
                     "VALUES('Spinner','classic',10,'[0,0,0]',18,180,0,'legacy-reference')")
    old = SlotManager(reopened).spin("Spinner", "classic", 10, "legacy-reference")
    assert old["spin"]["payout"] == 180 and old["balance"] == 1000
    # Spins from before tracking began (no rtp) count in neither the line stats nor the house's take.
    assert SlotManager(reopened).house() == house and SlotManager(reopened).lines("Spinner") == summary["lines"]
    # The house ledger backfills spins it never saw (here the legacy one, with no expected take).
    backfilled = HouseManager(reopened).summary()["games"][0]
    assert backfilled["all_time"]["rounds"] == 9 and backfilled["season"] == {"rounds": 1, "staked": 10, "take": -170, "expected": 0}
    assert reopened.query_one("SELECT expected FROM house_ledger WHERE ref='spin:9'")["expected"] is None
    reopened.conn.close()
    # A database whose slot_spins predates the rtp column gets it on open.
    import sqlite3
    legacy_path = os.path.join(shared.tmp, "slots-legacy.db")
    con = sqlite3.connect(legacy_path)
    con.execute("CREATE TABLE slot_spins (id INTEGER PRIMARY KEY AUTOINCREMENT, bettor TEXT NOT NULL, machine TEXT NOT NULL, "
                "stake REAL NOT NULL, reels TEXT NOT NULL, multiplier INTEGER NOT NULL, payout REAL NOT NULL, created_ts REAL NOT NULL, "
                "request_id TEXT NOT NULL, season_id INTEGER, UNIQUE(bettor, request_id))")
    con.commit()
    con.close()
    legacy = DB(legacy_path)
    assert "rtp" in {r["name"] for r in legacy.query("PRAGMA table_info(slot_spins)")}
    legacy.conn.close()
    # Golden Onkey payouts: the line and its doubling are both paid and both listed; line stats count the line it
    # filled in; the house counts it all as the secret symbol's.
    wild = DB(os.path.join(shared.tmp, "slots-wild.db"))
    wild.create_bettor("Goldie", 1000)
    wm = SlotManager(wild)
    for k, (reels, mult) in enumerate([([2, 2, WILD], 8), ([WILD, 3, WILD], 60), ([1, WILD, 4], 2)]):
        with patch("fivestack.slots.draw_spin", return_value=reels):
            out = wm.spin("Goldie", "jackpot", 10, f"golden-spin-{k:08d}")
        assert out["spin"]["payout"] == 10 * mult and out["spin"]["multiplier"] == mult
        assert out["spin"]["parts"] == payouts(m, reels)
    assert wild.get_bettor("Goldie")["balance"] == 1000 - 30 + 80 + 600 + 20
    # A spin recorded before Golden Onkeys paid on their own (a lone one, paid nothing) still shows it paid nothing.
    wild.execute("INSERT INTO slot_spins(bettor,machine,stake,reels,multiplier,payout,created_ts,request_id,rtp) "
                 "VALUES('Goldie','jackpot',10,'[6,0,1]',0,0,1,'before-spotted-1',0.95)")
    old_spin = wm.summary(wild.get_bettor("Goldie"))
    assert old_spin["history"][0]["parts"] == [] and old_spin["history"][1]["parts"][0]["kind"] == "spotted"
    assert old_spin["lines"]["hits"] == [0, 0, 1, 1, 0, 0, 0] and old_spin["me"]["wins"] == 3
    assert wm.house()["secret_paid"] == 700 and old_spin["machines"][0]["win_chance"] == win_chance(m)
    wild.conn.close()
    # Demo mode's machine makes every Golden Onkey outcome DEMO_GOLDEN_BOOST times as likely and leaves the regular
    # lines alone; the real machine is untouched.
    from fivestack.slots import DEMO_GOLDEN_BOOST, boosted
    demo_m = SlotManager(DB(os.path.join(shared.tmp, "slots-demo.db")), golden_boost=DEMO_GOLDEN_BOOST).machines["jackpot"]
    assert demo_m == boosted(m, DEMO_GOLDEN_BOOST) and demo_m["lines"][:WILD] == m["lines"][:WILD] and rtp(demo_m) == rtp(m)
    assert demo_m["lines"][WILD] == m["lines"][WILD] * DEMO_GOLDEN_BOOST and demo_m["spot1"] == m["spot1"] * DEMO_GOLDEN_BOOST
    assert MACHINES["jackpot"]["spot1"] == 3000 and SlotManager(db).machines["jackpot"] is MACHINES["jackpot"]


@section("stampede")
def stampede(shared):
    import random
    from fivestack import stampede as st
    from fivestack.house import HouseManager, casino_nets
    from fivestack.stampede import (BASE_STRIPS, FS_STRIPS, GOLDEN, JACKPOTS, METER_FULL, NINE, ONKEY, PICK_KINDS,
                                    SPIKE, TEN, WILD, StampedeManager, breakdown, hold_and_spin, pick_game, play, rtp,
                                    shape_kind, shape_wins, ways_wins)
    from fivestack.stampede import ACE, JACK, KING, QUEEN

    # The exact return is 95% (within a twentieth of a point), every part of it counted: ways wins (with the Inferno),
    # walls and squares, the Golden Onkey's spot pay, scatter pays, free spins (the spike plant's included), hold and
    # spin's fireballs and full-grid bonus, and the jackpots (each one's seed x how often the pick gives it, plus what
    # every spin grows it by).
    b = breakdown()
    assert abs(rtp() - 0.95) < 0.0005, rtp()
    assert abs(b["line"] + b["shapes"] + b["spot"] + b["scatter"] + b["free_spins"] + b["hold"] + b["jackpots"]
               - b["rtp"]) < 1e-12
    assert abs(b["jackpots"] - sum(b["jackpot_hits"][k] * j["seed"] + j["grow"] for k, j in JACKPOTS.items())) < 1e-12
    assert abs(b["pick_p"] - b["fires"] / METER_FULL) < 1e-12
    # Medium volatility: free spins about 1 spin in 100-150 (the spike plant finds some), hold and spin about 1 in
    # 150-250; the pick (its chances are per credit staked) about 1 spin in 250 at a 10-credit bet, and 10 times as
    # often at 100; the jackpots rarer and rarer. The spike plant about 1 spin in 75, the Golden Onkey about 1 in 230.
    assert 100 < 1 / b["fs_p"] < 150 and 150 < 1 / b["hs_p"] < 250 and 200 < 1 / (b["pick_p"] * 10) < 300
    assert 50 < 1 / b["plant_p"] < 100 and 150 < 1 / b["golden_p"] < 400
    hits = {k: v * 10 for k, v in b["jackpot_hits"].items()}
    assert 400 < 1 / hits["mini"] < 1 / hits["minor"] < 1 / hits["major"] < 1 / hits["grand"] < 100_000
    # The reels: no wild on reel 1, and a reel never shows two spikes. The Golden Onkey is a secret: never on a strip,
    # and what the page gets of the machine says nothing of what he pays.
    assert WILD not in BASE_STRIPS[0] and WILD not in FS_STRIPS[0]
    assert all(GOLDEN not in strip for strip in BASE_STRIPS + FS_STRIPS)
    described = st.machine()
    assert str(GOLDEN) not in described["pays"] and not any(k.startswith("golden") for k in described)
    for strip in BASE_STRIPS + FS_STRIPS:
        assert all(st.window(strip, i).count(SPIKE) <= 1 for i in range(len(strip)))
    # Ways: nines on reels 1-3 (two on reel 2) make 2 ways of 3; a wild stands in; a gap ends the run; spikes and
    # fireballs never pay ways.
    FIRE = st.FIRE
    grid = [[NINE, TEN, TEN, TEN], [NINE, NINE, TEN, FIRE], [WILD, TEN, FIRE, FIRE], [ONKEY] * 4, [ONKEY] * 4]
    wins = {w["symbol"]: w for w in ways_wins(grid)}
    assert wins[NINE]["ways"] == 2 and wins[NINE]["reels"] == 3 and wins[NINE]["mult"] == round(2 * st.PAYS[NINE][0], 4)
    assert wins[TEN]["ways"] == 3 * 1 * 2 and ONKEY not in wins
    assert len(wins[NINE]["cells"]) == 4  # the nine, two nines and the wild
    # The Golden Onkey is wild worth three ways on his reel.
    grid[2][0] = GOLDEN
    assert {w["symbol"]: w for w in ways_wins(grid)}[NINE]["ways"] == 2 * 3
    # Shapes: touching cells of one symbol (wilds joining in) make one group, which pays once for its shape. The two
    # Onkey reels and the Golden Onkey beside them are one group of nine (a mega block, not two walls and squares), x3
    # for him; the nines and he make a bent four, x3 too; the tens on reels 1 and 2 a four. The fireballs make no shape
    # (three bent ones), and a group of two (the Golden Onkey and the ten under him) pays nothing.
    shapes = shape_wins(grid, values={(1, 3): 1, (2, 2): 1, (2, 3): 1})
    assert [(x["kind"], x["symbol"], x["x"], x["mult"]) for x in shapes] == [
        ("four", NINE, 3, round(st.SHAPE_BASE["low"] * st.SHAPE_FACTOR["four"] * 3, 4)),
        ("four", TEN, 1, round(st.SHAPE_BASE["low"] * st.SHAPE_FACTOR["four"], 4)),
        ("mega", ONKEY, 3, round(st.SHAPE_BASE["top"] * st.SHAPE_FACTOR["mega"] * 3, 4))], shapes
    assert shapes[2]["cells"] == [[2, 0]] + [[c, r] for c in (3, 4) for r in range(4)]
    # A 2 x 3 block of nines is one block, not squares and rows; three fireballs in a row pay a share of their values.
    grid2 = [[NINE, NINE, ACE, FIRE], [NINE, NINE, KING, FIRE], [NINE, NINE, ACE, FIRE], [QUEEN, KING, KING, JACK],
             [ACE, KING, QUEEN, JACK]]
    shapes = shape_wins(grid2, values={(0, 3): 1, (1, 3): 2, (2, 3): 5})
    assert [(x["kind"], x["symbol"], x["mult"]) for x in shapes] == [
        ("block", NINE, round(st.SHAPE_BASE["low"] * st.SHAPE_FACTOR["block"], 4)), ("three", FIRE, round(st.FIRE_SHARE * 8, 4))]
    # A group of wilds alone is no shape; a fireball group never takes in wilds.
    assert shape_wins([[SPIKE, FIRE, SPIKE, FIRE], [WILD] * 4, [FIRE, SPIKE, FIRE, SPIKE], [SPIKE, FIRE, SPIKE, FIRE],
                       [FIRE, SPIKE, FIRE, SPIKE]], values={(c, r): 1 for c in range(5) for r in range(4)}) == []
    # A spicy banana is wild for every symbol (here it finishes three tens on reels 1-3), and a sliced reel doubles
    # every way and shape through it.
    SPICY = st.SPICY
    grid3 = [[TEN, ACE, KING, QUEEN], [TEN, KING, ACE, QUEEN], [SPICY, QUEEN, KING, ACE], [ACE, KING, QUEEN, JACK],
             [KING, ACE, JACK, QUEEN]]
    tens = {w["symbol"]: w for w in ways_wins(grid3)}[TEN]
    assert tens["ways"] == 1 and tens["reels"] == 3 and tens["cells"] == [[0, 0], [1, 0], [2, 0]]
    assert [(x["kind"], x["symbol"]) for x in shape_wins(grid3)] == [("three", TEN)]
    assert {w["symbol"]: w for w in ways_wins(grid3, sliced=1)}[TEN]["ways"] == 2
    assert shape_wins(grid3, sliced=1)[0]["x"] == st.SLICE_MULT and shape_wins(grid3, sliced=4)[0]["x"] == 1
    # The four events come equally often; spicy bananas are only on reels 3-5, and never in free spins.
    assert len(set(st.EVENTS.values())) == 1 and set(st.EVENTS) == {"stampede", "rain", "greg", "slice"}
    assert all((SPICY in strip) == (c >= 2) for c, strip in enumerate(BASE_STRIPS))
    assert all(SPICY not in strip for strip in FS_STRIPS) and 5 < 1 / b["spicy_p"] < 10
    # The shapes themselves: a straight line of three (not a bent one), a wall, a square, a four, five and up by size.
    assert shape_kind(3, [(0, 0), (0, 1), (0, 2)]) == shape_kind(3, [(0, 1), (1, 1), (2, 1)]) == "three"
    assert shape_kind(3, [(0, 0), (0, 1), (1, 1)]) is None and shape_kind(2, [(0, 0), (0, 1)]) is None
    assert shape_kind(4, [(1, r) for r in range(4)]) == "wall" and shape_kind(4, [(0, 0), (0, 1), (1, 0), (1, 1)]) == "square"
    assert shape_kind(4, [(c, 2) for c in range(4)]) == shape_kind(4, [(0, 0), (1, 0), (1, 1), (2, 1)]) == "four"
    assert [shape_kind(n) for n in (5, 6, 7, 8, 12)] == ["five", "block", "block", "mega", "mega"]
    # Free spins' wilds multiply: x2 on reel 2 and x3 on reel 3 make one way worth six.
    grid = [[NINE, TEN, TEN, TEN], [WILD, TEN, TEN, TEN], [WILD, TEN, TEN, TEN], [TEN] * 4, [TEN] * 4]
    fs = {w["symbol"]: w for w in ways_wins(grid, {(1, 0): 2, (2, 0): 3})}
    assert fs[NINE]["ways"] == 6 and fs[NINE]["mult"] == round(6 * st.PAYS[NINE][0], 4)
    # The exact math agrees with play(): 30,000 spins from a seeded generator land within a few standard errors, and
    # most paying spins pay back more than the stake.
    rng = random.Random(11)
    n, line, shape, fs_n, hs_n, fires, paid, plants, goldens = 30000, 0.0, 0.0, 0, 0, 0, 0, 0, 0
    within = lambda got, p: abs(got / n - p) < 4 * (p / n) ** 0.5
    for _ in range(n):
        r = play(rng.randrange)
        line += r["line_mult"]
        shape += r["shape_mult"]
        fs_n += bool(r["free_spins"])
        hs_n += bool(r["hold"])
        fires += len(r["values"])
        paid += st.cash_mult(r) > 0
        # Onkey's Inferno multiplies the shapes with the ways, and only comes on a spin that won one or the other.
        # A spicy banana's pepper multiplies every ways and shape win, all of them together, only on a spin that won.
        ev = r["event"] or {}
        sliced = ev.get("reel") if ev.get("kind") == "slice" else None
        fresh = shape_wins(r["grid"], values={(c, row): v for c, row, v in r["values"]}, sliced=sliced)
        assert [x["mult"] for x in r["shapes"]] == [round(x["mult"] * (r["heat"] or 1), 4) for x in fresh]
        assert [[c, row] for c, row, _ in r["spicy"]] == [[c, row] for c in range(5) for row in range(4)
                                                         if r["grid"][c][row] == SPICY]
        if r["spicy"] and (r["wins"] or r["shapes"]):
            assert r["heat"] == st._prod(m for _, _, m in r["spicy"])
        else:
            assert r["heat"] is None
        # Greg's takeover: a Greg on each of reels 1-3, so a Greg win; the slice picks one reel.
        if ev.get("kind") == "greg":
            assert all(r["grid"][c][row] == st.GREG for c, row in ev["cells"]) and {c for c, _ in ev["cells"]} >= {0, 1, 2}
            assert any(w["symbol"] == st.GREG for w in r["wins"])
        # Free spins pay their shapes too.
        for spin in (r["free_spins"] or {}).get("spins", []):
            assert spin["mult"] == round(sum(w["mult"] for w in spin["wins"]) + sum(x["mult"] for x in spin["shapes"])
                                         + (spin["scatter"]["mult"] if spin["scatter"] else 0), 4)
        assert st.cash_mult(r) == round(r["line_mult"] + r["shape_mult"] + r["spot"]
                                        + (r["scatter"]["mult"] if r["scatter"] else 0)
                                        + (r["free_spins"]["mult"] if r["free_spins"] else 0)
                                        + (r["hold"]["cash"] if r["hold"] else 0), 4)
        if r["golden"]:
            # Only on a spin without an event, on a cell that held a paying symbol, never with the plant.
            c, row = r["golden"]
            goldens += 1
            assert r["grid"][c][row] == GOLDEN and r["landed"][c][row] < WILD and not r["event"] and not r["plant"]
            assert r["spot"] == st.GOLDEN_SPOT
        if r["plant"]:
            # Two spikes as it landed; the reels without one spun again (the others as they landed), and it's found
            # when the grid shows a third.
            plants += 1
            pl = r["plant"]
            assert sum(col.count(SPIKE) for col in r["landed"]) == 2 and not r["event"]
            assert pl["reels"] == [c for c in range(5) if SPIKE not in r["landed"][c]]
            assert all(r["grid"][c] == (pl["landed"][pl["reels"].index(c)] if c in pl["reels"] else r["landed"][c])
                       for c in range(5))
            assert pl["found"] == (sum(col.count(SPIKE) for col in r["grid"]) >= 3) == bool(r["free_spins"])
    assert abs(line / n - b["line"]) < 0.05, (line / n, b["line"])
    assert abs(shape / n - b["shapes"]) < 0.03, (shape / n, b["shapes"])
    assert within(fs_n, b["fs_p"]) and within(hs_n, b["hs_p"]) and within(plants, b["plant_p"])
    assert within(goldens, b["golden_p"])
    # Plenty of visual wins: about 2 spins in 3 pay something (mostly the small shapes).
    assert abs(fires / n - b["fires"]) < 0.03 and 0.6 < paid / n < 0.78
    # Hold and spin: from six fireballs it ends with about the exact expected count, each new one resets the respins
    # to three, and it pays every fireball's value (plus the bonus on a full grid).
    start = [[c, 0, 1] for c in range(5)] + [[0, 1, 1]]
    finals = []
    for _ in range(4000):
        h = hold_and_spin(rng.randrange, start)
        held = len(start) + sum(len(x["new"]) for x in h["rounds"])
        finals.append(held)
        assert all(x["respins"] == 3 for x in h["rounds"] if x["new"]) and (h["rounds"][-1]["respins"] == 0 or h["full"])
        assert h["cash"] == h["values"] + (st.FULL_GRID_BONUS if h["full"] else 0)
        assert h["values"] == 6 + sum(v for x in h["rounds"] for _, _, v in x["new"])
    assert abs(sum(finals) / len(finals) - st._hs_math()[6][0]) < 0.15
    # The pick: three of each kind on the board; the clicks end on the outcome's third, with every other kind shown
    # at most twice; smoke three times means no jackpot.
    for outcome in ("mini", "minor", "major", "grand", None):
        g = pick_game(outcome, rng.randrange)
        win = outcome or "smoke"
        assert sorted(g["picks"] + g["rest"]) == sorted(k for k in PICK_KINDS for _ in range(3))
        assert g["picks"][-1] == win and g["picks"].count(win) == 3
        assert all(g["picks"].count(k) <= 2 for k in PICK_KINDS if k != win)

    db = DB(os.path.join(shared.tmp, "stampede.db"))
    db.create_bettor("Stomper", 1000)
    db.create_bettor("Broke", 1)
    bets = BetManager({"starting_balance": 1000}, db, shared.engine)
    house_mgr = HouseManager(db, bets)
    manager = StampedeManager(db)
    seeds = {p["key"]: p["size"] for p in manager.pots()}
    assert seeds == {k: j["seed"] for k, j in JACKPOTS.items()}
    wheel_before = house_mgr.summary_pots()[1]
    # A spin takes the stake and pays its multiple; each fireball adds the stake to the meter; a retry with the same
    # reference returns the same spin; every spin grows every jackpot by its share of the stake.
    out = manager.spin("Stomper", 10, "stomp-0000000000000001")
    spin = out["spin"]
    assert spin["payout"] == round(10 * spin["result"]["mult"], 2) and out["balance"] == round(1000 - 10 + spin["payout"], 2)
    assert out["meter"]["heat"] == 10 * len(spin["result"]["values"]) == spin["result"]["meter"]["after"]
    again = manager.spin("Stomper", 10, "stomp-0000000000000001")
    assert again["spin"]["id"] == spin["id"] and again["balance"] == out["balance"] and again["meter"] == out["meter"]
    _expect_error(manager.spin, "Stomper", 25, "stomp-0000000000000001", contains="already been used")
    _expect_error(manager.spin, "Stomper", 7, "stomp-0000000000000002", contains="stake")
    _expect_error(manager.spin, "Stomper", 10, "short", contains="reference")
    _expect_error(manager.spin, "Broke", 2, "stomp-0000000000000003", contains="Not enough")
    grown = {p["key"]: p["size"] for p in manager.pots()}
    assert all(abs(grown[k] - seeds[k] - 10 * JACKPOTS[k]["grow"]) < 1e-6 for k in JACKPOTS)
    # Only demo mode can ask for a spin: the real machine ignores `force` (no pick unless the meter really filled).
    plain = manager.spin("Stomper", 2, "stomp-0000000000000004", force="grand")
    assert not plain["spin"]["result"].get("pick") and not plain["spin"]["result"]["jackpots"]
    assert plain["meter"]["heat"] == out["meter"]["heat"] + 2 * len(plain["spin"]["result"]["values"])
    # Demo mode: a forced Mini fills the meter, and the pick pays the Mini's size in credits (the same at any bet),
    # then resets the Mini and empties the meter; anything over the top carries on.
    demo = StampedeManager(db, demo=True)
    db.execute("DELETE FROM stampede_meters")
    mini_size = {p["key"]: p["size"] for p in demo.pots()}["mini"] + 5 * JACKPOTS["mini"]["grow"]
    won = demo.spin("Stomper", 5, "stomp-0000000000000005", force="mini")["spin"]
    r = won["result"]
    assert r["pick"]["outcome"] == "mini" and r["pick"]["picks"][-1] == "mini"
    jp = r["jackpots"][0]
    assert jp["key"] == "mini" and abs(jp["amount"] - round(mini_size, 2)) < 0.006
    assert won["payout"] == round(5 * r["cash_mult"] + jp["amount"], 2)
    assert demo.meter("Stomper")["heat"] == r["meter"]["after"] == r["meter"]["before"] + r["meter"]["added"] - METER_FULL
    assert {p["key"]: p for p in demo.pots()}["mini"]["size"] == JACKPOTS["mini"]["seed"]
    # A bigger bet fills the meter faster: 100 a fireball against 2.
    db.execute("INSERT OR REPLACE INTO stampede_meters(bettor, heat) VALUES('Stomper', ?)", (METER_FULL - 1,))
    grand = demo.spin("Stomper", 100, "stomp-0000000000000006", force="grand")["spin"]
    g = grand["result"]
    assert g["meter"]["added"] == 100 * len(g["values"]) and g["jackpots"][-1]["key"] == "grand"
    assert g["jackpots"][-1]["amount"] >= JACKPOTS["grand"]["seed"]
    pots = {p["key"]: p for p in demo.pots()}
    assert pots["grand"]["size"] == JACKPOTS["grand"]["seed"] and pots["grand"]["hits"] == 1
    # Three smokes: the meter empties and nothing is paid from the jackpots.
    smoke = demo.spin("Stomper", 2, "stomp-0000000000000007", force="smoke")["spin"]["result"]
    assert smoke["pick"]["outcome"] is None and smoke["pick"]["picks"][-1] == "smoke" and smoke["jackpots"] == []
    # A forced full grid pays the bonus.
    full = demo.spin("Stomper", 2, "stomp-0000000000000008", force="full")["spin"]["result"]
    assert full["hold"]["full"] and full["hold"]["cash"] == full["hold"]["values"] + st.FULL_GRID_BONUS
    assert db.query_one("SELECT COUNT(*) AS n FROM stampede_jackpots WHERE key='grand'")["n"] == 1
    assert demo.summary()["jackpot_log"][0]["key"] == "grand"
    # The house: a ledger row per spin with the edge's expected take, which grows the daily wheel's jackpot; nothing
    # the machine pays (jackpots included) comes out of the house's pot or the wheel's jackpot.
    rows = db.query("SELECT * FROM house_ledger WHERE game='stampede'")
    count = db.query_one("SELECT COUNT(*) AS n FROM stampede_spins")["n"]
    assert len(rows) == count == 6
    assert all(abs(r["expected"] - r["staked"] * (1 - rtp())) < 1e-3 for r in rows)
    assert db.query_one("SELECT COUNT(*) AS n FROM house_payouts")["n"] == 0
    assert house_mgr.summary_pots()[1] > wheel_before
    # The season: its net counts in the casino column, kept out of match-betting profit; a reset tags the spins and
    # keeps the jackpots and the meters where they are.
    net = round(db.get_bettor("Stomper")["balance"] - 1000, 2)
    assert casino_nets(db)["stomper"]["stampede"] == net
    row = next(r for r in bets.leaderboard() if r["name"] == "Stomper")
    assert row["stampede"] == net and row["casino"] == net and row["profit"] == 0
    me = demo.summary(db.get_bettor("Stomper"))
    assert me["me"]["spins"] == 6 and me["me"]["picks"] == 3 and me["me"]["net"] == net and me["meter"]["full"] == METER_FULL
    kept, meter = demo.pots(), demo.meter("Stomper")
    season = bets.reset()
    assert db.query_one("SELECT COUNT(*) AS n FROM stampede_spins WHERE season_id=?", (season["id"],))["n"] == 6
    assert demo.summary(db.get_bettor("Stomper"))["me"]["spins"] == 0 and demo.pots() == kept
    assert demo.meter("Stomper") == meter
    # Old retry keys survive the reset.
    assert manager.spin("Stomper", 10, "stomp-0000000000000001")["spin"]["id"] == spin["id"]
    db.conn.close()


def _expect_error(fn, *args, contains=""):
    try:
        fn(*args)
    except BetError as e:
        assert contains in str(e), (contains, str(e))
        return str(e)
    raise AssertionError(f"expected an error containing {contains!r}")


@section("blackjack")
def blackjack(shared):
    import threading
    from contextlib import contextmanager
    from unittest.mock import patch
    from fivestack import bjstrategy, cards
    from fivestack.blackjack import EDGE, SEATS_EACH, BlackjackManager, is_blackjack, outcome, total
    from fivestack.house import HouseManager, casino_nets

    assert total(["As", "Kd"]) == (21, True) and is_blackjack(["As", "Kd"]) and not is_blackjack(["As", "Kd"], split=True)
    assert total(["As", "Ah", "9c"]) == (21, True) and total(["Kh", "Qd", "2c"]) == (22, False)
    assert total(["As", "6d", "Kc"]) == (17, False)
    hand = lambda cs, stake=10, split=False: {"cards": cs, "stake": stake, "split": split}
    assert outcome(hand(["As", "Kd"]), ["Th", "9c"]) == ("blackjack", 25)
    assert outcome(hand(["As", "Kd"]), ["Ah", "Qc"]) == ("push", 10)
    assert outcome(hand(["Th", "9d"]), ["Ah", "Qc"]) == ("lose", 0)
    assert outcome(hand(["Th", "9d"]), ["Th", "6c", "Kd"]) == ("win", 20)
    assert outcome(hand(["Th", "9d", "5c"]), ["Th", "6c", "Kd"]) == ("bust", 0)
    assert outcome(hand(["Th", "7d"]), ["Th", "7c"]) == ("push", 10)
    # Onkey's hint is basic strategy for these rules, and the house edge it implies is EDGE.
    best = lambda cs, up, moves=("hit", "stand", "double"): bjstrategy.best(cs, up, moves)["move"]
    assert best(["Th", "6c"], "Ks") == "hit" and best(["Th", "3c"], "4s") == "stand" and best(["6h", "5c"], "6s") == "double"
    assert best(["6h", "5c"], "As") == "hit" and best(["Ah", "7c"], "9s") == "hit" and best(["Ah", "7c"], "4s") == "double"
    assert best(["Ah", "7c"], "4s", ("hit", "stand")) == "stand"  # can't double a third card: then soft 18 stands
    assert best(["8h", "8c"], "As", ("hit", "stand", "double", "split")) == "split"
    assert best(["Th", "Kc"], "6s", ("hit", "stand", "double", "split")) == "stand"
    assert best(["9h", "9c"], "7s", ("hit", "stand", "double", "split")) == "stand"
    assert best(["Ah", "Ac"], "Ts", ("stand", "split")) == "split" and bjstrategy.best(["Th", "Kc"], "6s", ()) is None
    ev = bjstrategy.values(["Th", "6c"], "Ks", ("hit", "stand"))
    assert -0.56 < ev["stand"] < -0.52 and ev["hit"] > ev["stand"]  # 16 against a ten: hitting loses a hair less
    # The hint knows what the balance covers (`spare` stakes). Fours against a 5 only pay as a split if you can double
    # after, so on the last stake they're hit; eights split whatever's left. A split is worth more the more its hands
    # can afford, up to what it's worth with no limit, and ROOMY spare stakes is as good as that.
    four = ("hit", "stand", "double", "split")
    assert bjstrategy.best(["4h", "4c"], "5s", four)["move"] == "split" and bjstrategy.best(["4h", "4c"], "5s", four, 1)["move"] == "hit"
    assert bjstrategy.best(["8h", "8c"], "6s", four, 1)["move"] == "split" and bjstrategy.best(["Ah", "Ac"], "6s", four, 1)["move"] == "split"
    by_spare = [bjstrategy.values(["8h", "8c"], "6s", four, n)["split"] for n in range(1, bjstrategy.ROOMY)]
    free = bjstrategy.values(["8h", "8c"], "6s", four)["split"]
    assert by_spare == sorted(by_spare) and by_spare[0] < free - 0.1 and 0 <= free - by_spare[-1] < 0.001, (by_spare, free)
    assert bjstrategy.values(["8h", "8c"], "6s", four, bjstrategy.ROOMY) == bjstrategy.values(["8h", "8c"], "6s", four)
    # A double is charged for the stake it takes from the split hands behind it: 11 against a 6 doubles on the last
    # stake, unless a pair of eights is waiting to be split with it.
    three = ("hit", "stand", "double")
    assert bjstrategy.best(["8h", "3c"], "6s", three, 1)["move"] == "double" and bjstrategy.best(["8h", "3c"], "6s", three, 1, ["2d"])["move"] == "double"
    assert bjstrategy.best(["8h", "3c"], "6s", three, 1, ["8d"])["move"] == "hit"
    assert bjstrategy.values(["8h", "3c", "2d"], "6s", ("hit", "stand"), 0) == bjstrategy.values(["8h", "3c", "2d"], "6s", ("hit", "stand"))
    assert abs(bjstrategy.house_edge() - EDGE) < 0.00005

    db = DB(os.path.join(shared.tmp, "casino.db"))
    for n in ("Ace", "Bea", "Cy", "S1", "S2", "S3"):
        db.create_bettor(n, 1000)
    db.create_bettor("Broke", 3)
    clock = [1000.0]
    bj = BlackjackManager(db, clock=lambda: clock[0])
    filler = cards.deck(2)

    @contextmanager
    def stack(*cs):  # the next deal comes from a fresh shoe starting with these cards
        for t in bj.tables.values():
            t.shoe = []
        with patch("fivestack.cards.shuffled", return_value=list(cs) + filler):
            yield
    bal = lambda n: db.get_bettor(n)["balance"]
    ref = iter(f"bj-reference-{i:08d}" for i in range(1000))

    # Solo: the deal goes you, the dealer, you, the dealer. 10 + 7 hits a 4 for 21 (which stands by itself); the
    # dealer's 9 + 8 stands on 17, and you win even money.
    with stack("Th", "9s", "7d", "8c", "4h"):
        r1 = next(ref)
        v = bj.bet("Ace", "solo", 10, r1)
        assert bal("Ace") == 990 and v["phase"] == "playing" and v["me"]["actions"] == ["hit", "stand", "double"]
        assert v["me"]["hint"]["move"] == "stand" and set(v["me"]["hint"]["ev"]) == {"hit", "stand", "double"}  # 17 v 9
        assert v["dealer"]["cards"] == ["9s", None] and v["dealer"]["total"] == 9  # the hole card stays hidden
        _expect_error(bj.action, "Ace", "solo", "hit", 5, contains="moved on")
        _expect_error(bj.action, "Ace", "solo", "split", 0, contains="can't do that")
        v = bj.action("Ace", "solo", "hit", 0)
    assert v["phase"] == "done" and v["dealer"]["cards"] == ["9s", "8c"] and v["seats"][0]["hands"][0]["result"] == "win"
    assert bal("Ace") == 1010
    assert bj.bet("Ace", "solo", 10, r1)["me"]["balance"] == 1010  # a retried bet isn't charged again
    row = db.query_one("SELECT * FROM blackjack_hands WHERE request_id=?", (r1,))
    assert row["status"] == "settled" and row["stake"] == 10 and row["payout"] == 20
    assert db.query_one("SELECT take, expected FROM house_ledger WHERE ref=?", (f"hand:{row['id']}",)) == {"take": -10, "expected": 0.011}
    # A natural pays 3:2 straight away (the dealer's 9 up means no peek, and the dealer doesn't draw).
    with stack("As", "9s", "Kd", "7c"):
        v = bj.bet("Ace", "solo", 10, next(ref))
    assert v["phase"] == "done" and v["seats"][0]["hands"][0]["result"] == "blackjack" and v["dealer"]["cards"] == ["9s", "7c"]
    assert bal("Ace") == 1025
    # The dealer peeks under an ace: a dealer blackjack takes only the stake, before you can double or split.
    with stack("Th", "As", "9d", "Kc"):
        v = bj.bet("Ace", "solo", 10, next(ref))
    assert v["phase"] == "done" and v["dealer"]["blackjack"] and bal("Ace") == 1015
    # Doubling 11 into 21 against the dealer's 17 wins the doubled stake.
    with stack("6h", "Ts", "5d", "7c", "Tc"):
        bj.bet("Ace", "solo", 10, next(ref))
        assert bal("Ace") == 1005
        v = bj.action("Ace", "solo", "double", 0)
    assert v["seats"][0]["hands"][0]["doubled"] and v["seats"][0]["hands"][0]["payout"] == 40 and bal("Ace") == 1035
    # Splitting eights: each hand takes a card (10 and K, 18 each), both stand, the dealer's 16 draws a K and busts.
    with stack("8h", "9s", "8d", "7c", "Tc", "Kd", "Kc"):
        bj.bet("Ace", "solo", 10, next(ref))
        v = bj.action("Ace", "solo", "split", 0)
        assert bal("Ace") == 1015 and [h["total"] for h in v["seats"][0]["hands"]] == [18, 18]
        assert v["me"]["actions"] == ["hit", "stand", "double"]  # double after a split; 8 + 10 isn't a pair to split again
        bj.action("Ace", "solo", "stand", 1)
        v = bj.action("Ace", "solo", "stand", 2)
    assert [h["result"] for h in v["seats"][0]["hands"]] == ["win", "win"] and bal("Ace") == 1055
    assert db.query_one("SELECT stake, payout FROM blackjack_hands ORDER BY id DESC LIMIT 1") == {"stake": 20, "payout": 40}
    assert v["me"]["season"]["hands"] == 5 and v["me"]["season"]["net"] == 55 and v["me"]["season"]["blackjacks"] == 1
    _expect_error(bj.bet, "Broke", "solo", 5, next(ref), contains="Not enough credits")
    _expect_error(bj.bet, "Ace", "solo", 7, next(ref), contains="Choose a stake")
    _expect_error(bj.bet, "Ace", "solo", 5, "short", contains="reference")

    # The shared table: five seats.
    for n in ("Ace", "Bea", "S1", "S2", "S3"):
        bj.sit(n)
    _expect_error(bj.sit, "Cy", contains="full")
    for n in ("S1", "S2", "S3"):
        bj.leave(n)
    _expect_error(bj.bet, "Cy", "shared", 10, next(ref), contains="Take a seat")
    # Betting closes when everyone seated has bet. Ace 10 + 6, Bea a pair of nines, the dealer 7 + 10.
    with stack("Th", "9h", "7s", "6d", "9c", "Ts"):
        v = bj.bet("Ace", "shared", 10, next(ref))
        assert v["phase"] == "betting" and v["deadline"] == 1015
        v = bj.bet("Bea", "shared", 10, next(ref))
    assert v["phase"] == "playing" and v["turn"] == {"bettor": "Ace", "hand": 0} and v["deadline"] == 1030
    assert bj.view("Bea", "shared")["me"]["actions"] == []
    _expect_error(bj.action, "Bea", "shared", "stand", 0, contains="not your turn")
    version = v["version"]
    bj.tick(1029)
    assert bj.view("Ace", "shared")["version"] == version  # nothing happened yet
    clock[0] = 1031
    bj.tick(1031)  # Ace's turn timed out: they stand on 16
    v = bj.view("Bea", "shared")
    assert v["turn"] == {"bettor": "Bea", "hand": 0} and "split" in v["me"]["actions"] and v["log"][-1]["kind"] == "stand"
    assert v["log"][-2]["kind"] == "timeout"
    v = bj.action("Bea", "shared", "stand", 0)
    assert v["phase"] == "done" and v["next_round_at"] == 1036
    assert bal("Ace") == 1045 and bal("Bea") == 1010
    _expect_error(bj.bet, "Ace", "shared", 10, next(ref), contains="Bets open again")
    bj.tick(1037)
    v = bj.view("Ace", "shared")
    assert v["phase"] == "betting" and v["round"] == 1 and v["seats"][0]["hands"] == []
    # With only one bet, betting closes BET_WINDOW_S after it.
    with stack("Th", "7s", "9h", "Ts"):
        clock[0] = 1040
        bj.bet("Ace", "shared", 25, next(ref))
        bj.tick(1050)
        assert bj.view("Ace", "shared")["phase"] == "betting"
        bj.tick(1056)
    assert bj.view("Ace", "shared")["phase"] == "playing"
    _expect_error(bj.leave, "Ace", contains="Finish your hand")
    clock[0] = 1060
    bj.action("Ace", "shared", "stand", 0)  # 19 against 17
    assert bal("Ace") == 1070
    bj.tick(1070)
    # A bet left before the deal comes back.
    bj.bet("Ace", "shared", 10, next(ref))
    bj.leave("Ace")
    assert bal("Ace") == 1070 and db.query_one("SELECT status FROM blackjack_hands ORDER BY id DESC LIMIT 1")["status"] == "void"
    # A hand in play when the server restarts (or the season ends) is refunded.
    with stack("Th", "9s", "6d", "8c"):
        bj.bet("Cy", "solo", 50, next(ref))
    assert bal("Cy") == 950
    BlackjackManager(db, clock=lambda: clock[0])
    assert bal("Cy") == 1000 and db.query_one("SELECT status, note FROM blackjack_hands ORDER BY id DESC LIMIT 1") == {
        "status": "void", "note": "The server restarted mid-hand"}
    # Long-polling wakes as soon as the table changes.
    woke = []
    t = bj.tables["shared"]
    waiter = threading.Thread(target=lambda: (bj.wait(t, t.version, 5), woke.append(time.time())))
    started = time.time()
    waiter.start()
    time.sleep(0.05)
    bj.sit("Cy")
    waiter.join(3)
    assert woke and woke[0] - started < 2
    # Season results: blackjack counts in the casino column; the ledger has a row per settled round.
    nets = casino_nets(db)
    assert nets["ace"] == {"total": 70, "blackjack": 70} and nets["bea"] == {"total": 10, "blackjack": 10} and "cy" not in nets
    games = {g["game"]: g for g in HouseManager(db).summary()["games"]}
    assert games["blackjack"]["season"]["rounds"] == 8 and games["blackjack"]["season"]["take"] == -80
    # Splits have no limit. Eights against a 9: split, a third eight splits again (the new hand goes next to its
    # pair), 8 + 2 doubles (the hint says so), and the dealer's 16 busts: three wins on 40 staked.
    db.create_bettor("Splitz", 1000)
    with stack("8h", "9s", "8d", "7c", "8c", "Kd", "Th", "2c", "Ks", "Qh"):
        bj.bet("Splitz", "solo", 10, next(ref))
        v = bj.action("Splitz", "solo", "split", 0)
        assert [h["cards"] for h in v["seats"][0]["hands"]] == [["8h", "8c"], ["8d", "Kd"]] and "split" in v["me"]["actions"]
        v = bj.action("Splitz", "solo", "split", 1)
        assert [h["total"] for h in v["seats"][0]["hands"]] == [18, 10, 18] and bal("Splitz") == 970
        assert [h["id"] for h in v["seats"][0]["hands"]] == [0, 2, 1]  # ids go by when a hand was made, not where it sits
        assert v["log"][-1] == {**v["log"][-1], "kind": "split", "hands": 3}
        v = bj.action("Splitz", "solo", "stand", 2)
        assert v["turn"]["hand"] == 1 and v["me"]["hint"]["move"] == "double"
        bj.action("Splitz", "solo", "double", 3)
        v = bj.action("Splitz", "solo", "stand", 4)
    assert [h["result"] for h in v["seats"][0]["hands"]] == ["win", "win", "win"] and bal("Splitz") == 1040
    assert db.query_one("SELECT stake, payout FROM blackjack_hands ORDER BY id DESC LIMIT 1") == {"stake": 40, "payout": 80}
    # Split aces take one card each, but a new ace can be split again (stand or split, nothing else); a split 21
    # pays even money, not 3 to 2.
    with stack("Ah", "9s", "Ad", "7c", "As", "5d", "Kh", "Qc", "Th"):
        bj.bet("Splitz", "solo", 10, next(ref))
        v = bj.action("Splitz", "solo", "split", 0)
        assert v["turn"]["hand"] == 0 and v["me"]["actions"] == ["stand", "split"] and v["me"]["hint"]["move"] == "split"
        assert v["seats"][0]["hands"][1]["done"]
        v = bj.action("Splitz", "solo", "split", 1)
    assert [h["total"] for h in v["seats"][0]["hands"]] == [21, 21, 16] and v["phase"] == "done"
    assert [h["result"] for h in v["seats"][0]["hands"]] == ["win", "win", "win"] and bal("Splitz") == 1070
    # Short of credits. A double or split the hand allows but the balance doesn't cover is left out of `actions` and
    # listed in `short`, and the hint counts what's affordable: with one stake left, fours against a 5 are a hit. Split
    # them anyway and the 11 that comes can't be doubled, nor the new pair split.
    db.create_bettor("Skint", 20)
    with stack("4h", "5s", "4d", "Tc", "7d", "4c", "9h", "2s"):
        v = bj.bet("Skint", "solo", 10, next(ref))
        assert v["me"]["actions"] == ["hit", "stand", "double", "split"] and v["me"]["short"] == [] and v["me"]["hint"]["move"] == "hit"
        v = bj.action("Skint", "solo", "split", 0)
        assert [h["cards"] for h in v["seats"][0]["hands"]] == [["4h", "7d"], ["4d", "4c"]] and bal("Skint") == 0
        assert v["me"]["actions"] == ["hit", "stand"] and v["me"]["short"] == ["double"] and v["me"]["hint"]["move"] == "hit"
        _expect_error(bj.action, "Skint", "solo", "double", 1, contains="can't do that")
        bj.action("Skint", "solo", "hit", 1)
        v = bj.action("Skint", "solo", "stand", 2)
        assert v["turn"]["hand"] == 1 and v["me"]["actions"] == ["hit", "stand"] and v["me"]["short"] == ["double", "split"]
        v = bj.action("Skint", "solo", "stand", 3)
    assert [h["result"] for h in v["seats"][0]["hands"]] == ["win", "lose"] and bal("Skint") == 20 and v["me"]["short"] == []
    # A split ace that draws another ace can only be split again. With no stake left for that there's nothing to
    # decide: it stands by itself (12), and here that ends the round.
    with stack("Ah", "6s", "Ad", "Tc", "As", "Kh", "5d"):
        bj.bet("Skint", "solo", 10, next(ref))
        v = bj.action("Skint", "solo", "split", 0)
    assert v["phase"] == "done" and [h["cards"] for h in v["seats"][0]["hands"]] == [["Ah", "As"], ["Ad", "Kh"]]
    assert [h["result"] for h in v["seats"][0]["hands"]] == ["lose", "push"] and bal("Skint") == 10
    assert [e["kind"] for e in v["log"] if e["kind"] in ("split", "stand")][-2:] == ["split", "stand"]
    # Onkey's peek: he names a card he shouldn't (only the claim reaches the page, decided once per decision), and the
    # truth comes out when the card shows. 16 against a 9: an honest look at the next card, a 4, which the hit draws.
    from fivestack import blackjack as bjmod
    with patch.object(bjmod, "PEEK_CHANCE", 1.0), patch.object(bjmod, "PEEK_HOLE", 0.0), patch.object(bjmod, "PEEK_LIE", 0.0):
        with stack("Th", "9s", "6d", "7c", "4h", "Ks"):
            v = bj.bet("Splitz", "solo", 10, next(ref))
            assert v["me"]["peek"] == {"kind": "next", "card": "4", "step": 0} and v["me"]["peek_result"] is None
            assert bj.view("Splitz", "solo")["me"]["peek"] == v["me"]["peek"]  # the same claim on every look
            v = bj.action("Splitz", "solo", "hit", 0)
            assert v["me"]["peek_result"] == {"kind": "next", "claimed": "4", "actual": "4", "honest": True, "step": 0}
            bj.action("Splitz", "solo", "stand", 1)
    # A lie about his hole card (a 7, so he names a small card), owned up to when the round ends.
    with patch.object(bjmod, "PEEK_CHANCE", 1.0), patch.object(bjmod, "PEEK_HOLE", 1.0), patch.object(bjmod, "PEEK_LIE", 1.0):
        with stack("Th", "9s", "6d", "7c", "4h"):
            v = bj.bet("Splitz", "solo", 10, next(ref))
            claim = v["me"]["peek"]
            assert claim["kind"] == "hole" and claim["card"] in "23456" and v["dealer"]["cards"][1] is None
            v = bj.action("Splitz", "solo", "stand", 0)
    assert v["phase"] == "done" and v["me"]["peek_result"] == {
        "kind": "hole", "claimed": claim["card"], "actual": "7", "honest": False, "step": 0}
    # Onkey's save: a king that busts 16 becomes a 5 of the same suit (crossed out on the page), the hand stands on 21.
    with patch.object(bjmod, "SAVE_CHANCE", 1.0), stack("Th", "9s", "6d", "7c", "Ks", "Qh"):
        bj.bet("Splitz", "solo", 10, next(ref))
        before = bal("Splitz")
        v = bj.action("Splitz", "solo", "hit", 0)
    h = v["seats"][0]["hands"][0]
    assert h["cards"] == ["Th", "6d", "5s"] and h["total"] == 21 and h["saved"] == {"index": 2, "was": "Ks"}
    assert any(e["kind"] == "save" and e["was"] == "Ks" and e["card"] == "5s" for e in v["log"])
    assert v["phase"] == "done" and h["result"] == "win" and bal("Splitz") == before + 20  # the dealer's 16 busts on the queen
    # Side bets: Perfect Pairs and 21+3, decided at the deal and paid with the hand; their edges are exact for six decks.
    from fivestack.blackjack import SIDE_EDGE, pairs_result, plus3_result, side_edge
    assert [pairs_result(*c) for c in (("8h", "8h"), ("8h", "8d"), ("8h", "8s"), ("8h", "9h"))] == ["perfect", "coloured", "mixed", None]
    assert plus3_result("7h", "7h", "7h") == "suited_trips" and plus3_result("7h", "7d", "7s") == "trips"
    assert plus3_result("9h", "Th", "Jh") == "straight_flush" and plus3_result("Ah", "2d", "3s") == "straight"
    assert plus3_result("Qh", "Kd", "As") == "straight" and plus3_result("Kh", "Ad", "2s") is None
    assert plus3_result("2h", "9h", "Kh") == "flush" and plus3_result("2h", "9d", "Kh") is None
    assert {k: round(side_edge(k), 4) for k in SIDE_EDGE} == SIDE_EDGE
    _expect_error(bj.bet, "Splitz", "solo", 10, next(ref), {"pairs": 5}, contains="closed for now")  # switched off
    side_open = patch.object(bjmod, "SIDE_BETS_OPEN", True)
    side_open.start()
    _expect_error(bj.bet, "Splitz", "solo", 10, next(ref), {"pairs": 25}, contains="bigger than your main bet")
    _expect_error(bj.bet, "Splitz", "solo", 10, next(ref), {"pairs": 7}, contains="same chips")
    _expect_error(bj.bet, "Splitz", "solo", 10, next(ref), {"lucky": 5}, contains="Unknown side bet")
    with stack("8h", "9s", "8d", "7c", "Ks"):  # a coloured pair of eights (12 to 1), no 21+3 with the 9
        before = bal("Splitz")
        v = bj.bet("Splitz", "solo", 10, next(ref), {"pairs": 5, "plus3": 5})
        assert bal("Splitz") == before - 20
        res = v["seats"][0]["side_results"]
        assert res["pairs"] == {"stake": 5, "result": "coloured", "name": "Coloured pair", "pays": 12, "payout": 65}
        assert res["plus3"]["result"] is None and res["plus3"]["payout"] == 0
        assert any(e["kind"] == "side" and e["bet"] == "pairs" and e["amount"] == 60 for e in v["log"])
        v = bj.action("Splitz", "solo", "stand", 0)  # 16 stands, the dealer's 16 busts on the king
    assert v["phase"] == "done" and bal("Splitz") == before - 20 + 20 + 65
    row = db.query_one("SELECT * FROM blackjack_hands ORDER BY id DESC LIMIT 1")
    assert row["stake"] == 20 and row["payout"] == 85 and json.loads(row["side"])["pairs"]["payout"] == 65
    assert db.query_one("SELECT expected FROM house_ledger WHERE ref=?", (f"hand:{row['id']}",))["expected"] == round(
        10 * EDGE + 5 * SIDE_EDGE["pairs"] + 5 * SIDE_EDGE["plus3"], 4)
    side_open.stop()
    # Streaks: three wins in a row are logged (and the seat carries the run); a loss turns it round.
    db.create_bettor("Streaky", 1000)
    for i in range(3):
        with stack("Th", "9s", "Tc", "7c", "Ks"):
            bj.bet("Streaky", "solo", 10, next(ref))
            v = bj.action("Streaky", "solo", "stand", 0)
    assert v["seats"][0]["streak"] == 3 and any(e["kind"] == "streak" and e["n"] == 3 for e in v["log"])
    with stack("Th", "Ts", "6c", "9c"):
        bj.bet("Streaky", "solo", 10, next(ref))
        v = bj.action("Streaky", "solo", "stand", 0)  # 16 against 19
    assert v["seats"][0]["streak"] == -1
    # Tips: after a win, up to what you won and once a round; off the balance, into the house's take and the casino net.
    with stack("Th", "9s", "Tc", "7c", "Ks"):
        bj.bet("Streaky", "solo", 10, next(ref))
        v = bj.action("Streaky", "solo", "stand", 0)  # 20 against a busted 26: +10
    assert v["me"]["tip"]["open"] and v["me"]["tip"]["max"] == 10 and v["me"]["tip"]["tipped"] is None
    before, row = bal("Streaky"), db.query_one("SELECT id FROM blackjack_hands ORDER BY id DESC LIMIT 1")["id"]
    take = db.query_one("SELECT take FROM house_ledger WHERE ref=?", (f"hand:{row}",))["take"]
    _expect_error(bj.tip, "Streaky", "solo", 25, contains="up to 10")
    _expect_error(bj.tip, "Streaky", "solo", 7, contains="Tip Onkey")
    v = bj.tip("Streaky", "solo", 10)
    assert bal("Streaky") == before - 10 and v["me"]["tip"]["tipped"] == 10 and not v["me"]["tip"]["open"]
    assert v["log"][-1]["kind"] == "tip" and v["log"][-1]["amount"] == 10
    assert db.query_one("SELECT tip FROM blackjack_hands WHERE id=?", (row,))["tip"] == 10
    assert db.query_one("SELECT take FROM house_ledger WHERE ref=?", (f"hand:{row}",))["take"] == take + 10
    assert casino_nets(db)["streaky"]["blackjack"] == 20 and v["me"]["season"]["net"] == 20  # +30 -10 +10, less the tip
    _expect_error(bj.tip, "Streaky", "solo", 5, contains="already has your tip")
    with stack("Th", "Ts", "6c", "9c"):
        bj.bet("Streaky", "solo", 10, next(ref))
        bj.action("Streaky", "solo", "stand", 0)  # a loss: nothing to tip from
    _expect_error(bj.tip, "Streaky", "solo", 5, contains="out of a win")
    # Emotes: one of the set, one at a time, and only at a table you're at.
    v = bj.emote("Streaky", "solo", "🍌")
    assert v["log"][-1]["kind"] == "emote" and v["log"][-1]["emote"] == "🍌"
    _expect_error(bj.emote, "Streaky", "solo", "👏", contains="One at a time")
    clock[0] += 2
    _expect_error(bj.emote, "Streaky", "solo", "🤡", contains="doesn't know")
    _expect_error(bj.emote, "Streaky", "shared", "👏", contains="Take a seat")
    # Several seats at the shared table: one bettor can hold up to SEATS_EACH of them while they're free, and a bet
    # puts the stake on each. Every seat is dealt a hand of its own, like so many players (a card each, then the dealer,
    # twice), and the bettor plays them left to right. Here 10 + 9 stands, the eights split (their 11 doubles into 21,
    # their 10 stands) and ace-king is a blackjack; the dealer's 16 busts. One row for the round; a hand keeps its seat
    # (`spot`) through a split, and the view lists each seat as its own entry.
    for _ in range(12):  # whatever the shared table was in the middle of: time it out and clear the seats
        clock[0] += 40
        bj.tick()
    for n in list(bj.tables["shared"].seats):
        bj.leave(n)
    for n, credits in (("Trio", 1000), ("Duo", 1000), ("Solo1", 40)):
        db.create_bettor(n, credits)
    _expect_error(bj.sit, "Trio", SEATS_EACH + 1, contains="Hold 1 to 3 seats")
    _expect_error(bj.sit, "Trio", "2", contains="Hold 1 to 3 seats")
    v = bj.sit("Trio", 3)
    assert v["me"]["seats"] == 3 and v["seats_each"] == SEATS_EACH == 3 and v["shared_seats"]["taken"] == 3
    assert [(s["bettor"], s["seat"]) for s in v["seats"]] == [("Trio", 0), ("Trio", 1), ("Trio", 2)]
    assert bj.view("Trio", "solo")["seats_each"] == 1 and bj.view("Trio", "solo")["me"]["seats"] == 1
    assert bj.sit("Duo", 2)["shared_seats"]["taken"] == 5
    _expect_error(bj.sit, "Solo1", contains="full")  # all five seats are held, by two bettors
    _expect_error(bj.sit, "Duo", 3, contains="Only 2 of the table's seats are free")
    assert bj.sit("Duo", 1)["shared_seats"]["taken"] == 4 and bj.sit("Duo")["me"]["seats"] == 1  # sitting again changes nothing
    bj.sit("Solo1")
    _expect_error(bj.sit, "Solo1", 2, contains="Only 1 of the table's seats is free")
    bj.leave("Solo1")
    bj.leave("Duo")
    _expect_error(bj.bet, "Trio", "shared", 500, next(ref), contains="for 3 seats at 500 each")
    with stack("Th", "8s", "Ah", "6d", "9c", "8d", "Kh", "Tc", "3d", "2h", "Ts", "9h"):
        v = bj.bet("Trio", "shared", 10, next(ref))
        seats = v["seats"]
        hands = [h for s in seats for h in s["hands"]]
        assert bal("Trio") == 970 and [s["stake"] for s in seats] == [10, 10, 10] and [len(s["hands"]) for s in seats] == [1, 1, 1]
        assert [h["cards"] for h in hands] == [["Th", "9c"], ["8s", "8d"], ["Ah", "Kh"]] and v["dealer"]["cards"] == ["6d", None]
        assert [(h["id"], h["spot"], h["stake"]) for h in hands] == [(0, 0, 10), (1, 1, 10), (2, 2, 10)]
        assert hands[2]["blackjack"] and hands[2]["done"] and v["turn"] == {"bettor": "Trio", "hand": 0}
        assert v["me"]["hint"]["move"] == "stand"
        _expect_error(bj.sit, "Trio", 1, contains="Your bet is down")
        v = bj.action("Trio", "shared", "stand", 0)
        assert v["turn"]["hand"] == 1 and "split" in v["me"]["actions"] and v["seats"][1]["hands"][0]["turn"]
        v = bj.action("Trio", "shared", "split", 1)
        seats = v["seats"]
        assert [[h["cards"] for h in s["hands"]] for s in seats] == [[["Th", "9c"]], [["8s", "3d"], ["8d", "2h"]], [["Ah", "Kh"]]]
        assert [[(h["id"], h["spot"]) for h in s["hands"]] for s in seats] == [[(0, 0)], [(1, 1), (3, 1)], [(2, 2)]]
        assert v["me"]["hint"]["move"] == "double"
        v = bj.action("Trio", "shared", "double", 2)
        assert v["turn"]["hand"] == 2
        v = bj.action("Trio", "shared", "stand", 3)
    assert v["phase"] == "done" and [[h["result"] for h in s["hands"]] for s in v["seats"]] == [["win"], ["win", "win"], ["blackjack"]]
    assert bal("Trio") == 1055 and v["seats"][0]["streak"] == 1 and v["me"]["seats"] == 3
    assert db.query_one("SELECT stake, payout FROM blackjack_hands ORDER BY id DESC LIMIT 1") == {"stake": 50, "payout": 105}
    assert bj.sit("Trio", 1)["me"]["seats"] == 1  # the round is over: seats can change again (the finished hands stay up)
    bj.leave("Trio")
    assert bj.view(None, "shared")["shared_seats"] == {"taken": 0, "max": 5, "names": []}
    shared.casino_db = db


@section("roulette")
def roulette(shared):
    from fivestack import roulette as R
    from fivestack.house import casino_nets

    db, bets = shared.db, shared.bets
    # The layout: every bet a real table takes, paying what a real table pays; the banana (zero) pays 36 to 1 straight,
    # which is exactly fair, and everything else carries the single-zero edge.
    kinds = {}
    for b in R.BETS.values():
        kinds[b["kind"]] = kinds.get(b["kind"], 0) + 1
    assert kinds == {"straight": 37, "split": 60, "street": 14, "corner": 23, "six": 11, "dozen": 3, "column": 3, "even": 6}, kinds
    assert len(R.WHEEL) == 37 and sorted(R.WHEEL) == list(range(37)) and len(R.REDS) == 18
    assert R.BETS["straight:0"]["pays"] == R.BANANA_PAYS == 36 and R.BETS["straight:17"]["pays"] == 35
    assert abs(R.edge("straight:0")) < 1e-12 and all(abs(R.edge(k) - 1 / 37) < 1e-12 for k in R.BETS if k != "straight:0")
    assert R.payout({"straight:17": 10, "red": 20, "split:17-20": 5}, 17) == (360 + 90, ["straight:17", "split:17-20"])
    assert R.payout({"red": 20, "dozen:1": 10, "corner:0-1-2-3": 5}, 0) == (45, ["corner:0-1-2-3"])  # the banana beats the rest
    assert R.payout({"straight:0": 5}, 0) == (185, ["straight:0"]) and R.colour(0) == "banana" and R.colour(1) == "red"
    for bad in (None, {}, {"nope": 5}, {"red": 3}, {"red": 7}, {"red": 5.5}, {"red": 505}, {"red": 300, "black": 250}, {"red": "5"},
                {k: 5 for k in list(R.BETS)[:R.MAX_SPOTS + 1]}):
        try:
            R.check_bets(bad)
            raise AssertionError(f"{bad} should be refused")
        except BetError:
            pass
    assert R.check_bets([{"key": "red", "amount": 5}, {"key": "red", "amount": 10}]) == {"red": 15}

    now = [2_000_000_000.0]
    rm = R.RouletteManager(db, clock=lambda: now[0])
    draws = []
    rm._draw = lambda: draws.pop(0)
    bets.register("Rou", "secret1")
    bets.register("Lette", "secret1")
    start = db.get_bettor("Rou")["balance"]
    board = {r["name"]: r for r in bets.leaderboard()}

    # The solo table: bets, the spin and the payout in one go, and a retry never charges twice.
    draws.append(17)
    v = rm.spin("Rou", {"straight:17": 10, "red": 20}, "roulette-solo-0001")
    assert v["table"] == "solo" and v["phase"] == "done" and v["result"] == 17 and v["colour"] == "black" and v["history"][0]["number"] == 17
    last = v["me"]["last"]
    assert last["staked"] == 30 and last["paid"] == 360 and last["net"] == 330 and last["won"] == ["straight:17"], last
    assert db.get_bettor("Rou")["balance"] == start + 330 and v["me"]["balance"] == start + 330
    assert rm.spin("Rou", {"straight:17": 10, "red": 20}, "roulette-solo-0001")["me"]["last"]["id"] == last["id"]
    assert db.get_bettor("Rou")["balance"] == start + 330 and not draws
    led = db.query_one("SELECT * FROM house_ledger WHERE game='roulette' AND ref=?", (f"spin:{last['id']}",))
    assert led["staked"] == 30 and led["take"] == -330 and abs(led["expected"] - 30 / 37) < 1e-3, dict(led)
    # The banana: a straight bet on it pays 36 to 1, and every bet that doesn't cover it loses.
    draws.append(0)
    v = rm.spin("Rou", {"straight:0": 5, "red": 50, "even": 50}, "roulette-solo-0002")
    assert v["colour"] == "banana" and v["me"]["last"]["paid"] == 185 and v["me"]["last"]["net"] == 80, v["me"]["last"]
    assert v["log"][-1]["kind"] == "banana" and v["log"][-1]["amount"] == 80
    for bad_bets, why in (({"red": 500, "black": 5}, "up to 500"),):
        try:
            rm.spin("Rou", bad_bets, "roulette-solo-0003")
            raise AssertionError(why)
        except BetError as e:
            assert why in str(e), e
    db.execute("UPDATE bettors SET balance = 20 WHERE name='Lette'")
    try:
        rm.spin("Lette", {"red": 25}, "roulette-solo-0004")
        raise AssertionError("not enough credits")
    except BetError as e:
        assert "Not enough credits" in str(e), e
    assert db.get_bettor("Lette")["balance"] == 20 and not db.query("SELECT 1 FROM roulette_spins WHERE bettor='Lette'")
    db.execute("UPDATE bettors SET balance = 1000 WHERE name='Lette'")

    # The shared table: chips go down while betting is open, the clock spins, and nobody sees who won until the wheel stops.
    balance = db.get_bettor("Rou")["balance"]
    v = rm.bet("Rou", {"red": 100}, "roulette-shared-0001")
    assert v["phase"] == "betting" and v["deadline"] == now[0] + R.BET_WINDOW_S and v["me"]["down"] == 100 and v["result"] is None
    now[0] += 5
    rm.bet("Lette", {"black": 50, "straight:0": 5}, "roulette-shared-0002")
    v = rm.bet("Rou", {"dozen:1": 50}, "roulette-shared-0003")
    assert v["me"]["down"] == 150 and v["me"]["bets"] == {"red": 100, "dozen:1": 50} and len(v["seats"]) == 2
    assert v["deadline"] == now[0] - 5 + R.BET_WINDOW_S  # more chips don't reopen the clock
    try:
        rm.bet("Rou", {"black": 400}, "roulette-shared-0004")
        raise AssertionError("over the table's limit across bets")
    except BetError as e:
        assert "150 down already" in str(e), e
    assert db.get_bettor("Rou")["balance"] == balance - 150
    rm.tick(now[0] + 1)
    assert rm.view("Rou", "shared")["phase"] == "betting"
    now[0] += R.BET_WINDOW_S
    draws.append(3)  # red, first dozen
    rm.tick()
    v = rm.view("Rou", "shared")
    assert v["phase"] == "spinning" and v["result"] == 3 and all(s["net"] is None for s in v["seats"]), v["seats"]
    assert not any(e["kind"] in ("win", "lose", "number") for e in v["log"])  # nothing gives it away yet
    assert db.get_bettor("Rou")["balance"] == balance - 150 + 200 + 150  # paid as the ball is let go: the page holds it
    try:
        rm.bet("Rou", {"red": 5}, "roulette-shared-0005")
        raise AssertionError("no more bets")
    except BetError as e:
        assert "No more bets" in str(e), e
    now[0] += R.SPIN_S
    rm.tick()
    v = rm.view("Lette", "shared")
    nets = {s["bettor"]: s["net"] for s in v["seats"]}
    assert v["phase"] == "done" and nets == {"Rou": 200, "Lette": -55} and v["history"][0] == {"number": 3, "colour": "red"}, (nets, v["history"])
    assert [e["kind"] for e in v["log"] if e["kind"] in ("win", "lose")] == ["win", "lose"] and v["me"]["last"]["net"] == -55
    now[0] += R.RESULT_S
    rm.tick()
    v = rm.view("Rou", "shared")
    assert v["phase"] == "betting" and v["seats"] == [] and v["result"] is None and v["me"]["down"] == 0 and v["deadline"] is None
    # Chips still waiting for a spin come back at start-up and on a season reset.
    balance = db.get_bettor("Lette")["balance"]
    rm.bet("Lette", {"red": 25}, "roulette-shared-0006")
    assert db.get_bettor("Lette")["balance"] == balance - 25
    with rm.lock:
        rm.void_open("test")
    assert db.get_bettor("Lette")["balance"] == balance and rm.view("Lette", "shared")["seats"] == []
    assert db.query_one("SELECT status, payout FROM roulette_spins WHERE request_id='roulette-shared-0006'") == {"status": "void", "payout": 25}
    # The season's numbers, and the leaderboard's casino column: roulette counts there and stays out of betting profit.
    season = rm.season("Rou")
    assert season["spins"] == 4 and season["wins"] == 4 and season["bananas"] == 1 and season["net"] == 330 + 80 + 200, season
    assert casino_nets(db)["rou"]["roulette"] == 610 and casino_nets(db)["lette"]["roulette"] == -55
    row = {r["name"]: r for r in bets.leaderboard()}["Rou"]
    assert row["casino"] == board["Rou"]["casino"] + 610 and abs(row["profit"] - board["Rou"]["profit"]) < 1e-9, row
    # A signed-out view of the layout has what the page needs to draw the table.
    lay = R.RouletteManager.layout()
    assert lay["table_max"] == 500 and lay["banana_pays"] == 36 and len(lay["bets"]) == 157 and lay["bets"]["red"]["pays"] == 1


@section("crash")
def crash(shared):
    import math
    from fivestack import crash as K
    from fivestack.house import casino_nets

    db, bets = shared.db, shared.bets
    # The draw: the chance the rocket reaches m is (1 - EDGE) / m, so every cash-out target is worth the same.
    assert K.crash_point(K.DRAW_N - 1) == 1.0 and K.crash_point(0) == K.MAX_MULT and K.crash_point(485_000 - 1) == 2.0
    assert K.crash_point(485_000) == 1.99 and K.crash_point(970_000 - 1) == 1.0 and K.crash_point(9_700) == 99.98
    points = [K.crash_point(r) for r in range(0, K.DRAW_N, 7)]
    for m in (1.01, 1.5, 2.0, 10.0, 100.0):
        share = sum(p >= m for p in points) / len(points)
        assert abs(share - (1 - K.EDGE) / m) < 2e-3 and abs(K.reach_chance(m) - (1 - K.EDGE) / m) < 1e-5, (m, share)
        assert abs(m * K.reach_chance(m) - (1 - K.EDGE)) < 1e-3  # what a credit on that target is worth
    assert sum(p == 1.0 for p in points) / len(points) > K.EDGE  # the rocket that never leaves the pad
    # The climb: e^(GROWTH x seconds), cut to cents, never past the top.
    assert K.multiplier_at(0) == 1.0 and K.multiplier_at(-3) == 1.0 and K.multiplier_at(1000) == K.MAX_MULT
    assert K.multiplier_at(K.time_to(2.0) + 1e-6) == 2.0 and K.multiplier_at(K.time_to(2.0) - 0.01) == 1.99
    assert abs(K.time_to(2.0) - math.log(2) / K.GROWTH) < 1e-12
    for bad in (None, 0, 7, 5.5, "5", 500, True):
        _expect_error(K.check_stake, bad, contains="Pick a stake")
    for bad in (1.0, 100.5, "2", -3, float("nan")):
        _expect_error(K.check_auto, bad, contains="Auto cash-out")
    assert K.check_auto(None) is None and K.check_auto("") is None and K.check_auto(2) == 2.0 and K.check_auto(1.239) == 1.24

    now = [2_100_000_000.0]
    km = K.CrashManager(db, clock=lambda: now[0])
    assert km.timers is False  # under a test clock nothing runs on a real timer
    draws = []
    km._draw = lambda: draws.pop(0)
    for name in ("Kra", "Shh", "Pad"):
        bets.register(name, "secret1")
    start = db.get_bettor("Kra")["balance"]
    board = {r["name"]: r for r in bets.leaderboard()}

    # The pad: one bet each, the launch a fixed time after the first, and a retry never charges twice.
    v = km.bet("Kra", 100, None, "crash-bet-00000001")
    assert v["phase"] == "betting" and v["round"] == 1 and v["deadline"] == now[0] + K.BET_WINDOW_S and v["crash"] is None
    assert v["me"]["bet"] == {"stake": 100, "auto": None, "cashout": None, "paid": 0} and db.get_bettor("Kra")["balance"] == start - 100
    assert km.bet("Kra", 100, None, "crash-bet-00000001")["me"]["balance"] == start - 100
    _expect_error(km.bet, "Kra", 50, None, "crash-bet-00000002", contains="already aboard")
    _expect_error(km.bet, "Nobody", 50, None, "crash-bet-00000003", contains="Sign in")
    _expect_error(km.bet, "Shh", 50, None, "short", contains="reference")
    db.execute("UPDATE bettors SET balance = 20 WHERE name='Pad'")
    _expect_error(km.bet, "Pad", 25, None, "crash-bet-00000004", contains="Not enough credits")
    db.execute("UPDATE bettors SET balance = 1000 WHERE name='Pad'")
    now[0] += 3
    v = km.bet("Shh", 50, 1.5, "crash-bet-00000005")
    assert v["deadline"] == now[0] - 3 + K.BET_WINDOW_S and len(v["seats"]) == 2  # a later bet doesn't move the launch
    km.bet("Pad", 25, 3.0, "crash-bet-00000006")
    _expect_error(km.cashout, "Kra", contains="not aboard")  # still on the pad
    km.tick(now[0] + 1)
    assert km.view()["phase"] == "betting"

    # The flight: the crash point stays on the server; an auto cash-out is paid at exactly its target; a cash-out by
    # hand at the multiplier when it arrives.
    launch = now[0] - 3 + K.BET_WINDOW_S
    draws.append(2.5)
    now[0] = launch + 0.1
    km.tick()
    v = km.view("Kra")
    assert v["phase"] == "flying" and v["started"] == launch and v["crash"] is None and "crash" not in [e["kind"] for e in v["log"]]
    assert "2.5" not in json.dumps({k: v[k] for k in v if k != "server_time"})  # nothing in the answer gives it away
    _expect_error(km.bet, "Kra", 5, None, "crash-bet-00000007", contains="has left")
    now[0] = launch + K.time_to(1.5) + 0.2
    km.tick()
    seats = {s["bettor"]: s for s in km.view()["seats"]}
    assert seats["Shh"]["cashout"] == 1.5 and seats["Shh"]["paid"] == 75 and seats["Pad"]["cashout"] is None, seats
    now[0] = launch + K.time_to(2.0) + 0.001
    v = km.cashout("Kra")
    assert v["me"]["bet"]["cashout"] == 2.0 and v["me"]["bet"]["paid"] == 200 and db.get_bettor("Kra")["balance"] == start + 100
    assert km.cashout("Kra")["me"]["balance"] == start + 100  # a second click pays nothing more
    led = db.query_one("SELECT * FROM house_ledger WHERE game='crash' AND bettor='Kra'")
    assert led["staked"] == 100 and led["take"] == -100 and abs(led["expected"] - 3) < 1e-9, dict(led)
    # The crash: whoever is still aboard loses the stake, and only now does the page learn where it crashed.
    pad = db.get_bettor("Pad")["balance"]
    now[0] = launch + K.time_to(2.5) + 0.01
    _expect_error(km.cashout, "Pad", contains="Too late: it crashed at 2.50x")
    v = km.view("Pad")
    assert v["phase"] == "crashed" and v["crash"] == 2.5 and v["history"] == [2.5] and db.get_bettor("Pad")["balance"] == pad
    seats = {s["bettor"]: s for s in v["seats"]}
    assert seats["Pad"]["lost"] and not seats["Kra"]["lost"] and v["me"]["last"]["net"] == -25 and v["me"]["last"]["crash"] == 2.5
    assert [e["kind"] for e in v["log"]][-4:] == ["cashout", "cashout", "crash", "lose"], [e["kind"] for e in v["log"]]
    assert {r["crash"] for r in db.query("SELECT crash FROM crash_bets WHERE round=1")} == {2.5}
    now[0] += K.RESULT_S
    km.tick()
    v = km.view("Kra")
    assert v["phase"] == "betting" and v["seats"] == [] and v["me"]["bet"] is None and v["deadline"] is None and v["history"] == [2.5]

    # A rocket that never leaves the pad (1.00x) takes every stake, auto cash-out or not.
    km.bet("Shh", 10, 1.01, "crash-bet-00000008")
    draws.append(1.0)
    now[0] += K.BET_WINDOW_S
    km.tick()
    v = km.view("Shh")
    assert v["phase"] == "crashed" and v["crash"] == 1.0 and v["me"]["last"]["net"] == -10 and v["round"] == 2
    now[0] += K.RESULT_S
    km.tick()
    # One that makes it to the top pays everyone still aboard there, and an auto target on the crash point itself is paid.
    km.bet("Kra", 5, None, "crash-bet-00000009")
    km.bet("Shh", 5, 100, "crash-bet-00000010")
    draws.append(K.MAX_MULT)
    now[0] += K.BET_WINDOW_S + K.time_to(K.MAX_MULT) + 1
    km.tick()
    v = km.view("Kra")
    assert v["phase"] == "crashed" and v["crash"] == 100.0 and [s["paid"] for s in v["seats"]] == [500, 500] and v["log"][-1]["moon"]
    now[0] += K.RESULT_S
    km.tick()

    # Bets on the pad or in the air come back at start-up and on a season reset; ones already cashed out stand.
    km.bet("Pad", 25, None, "crash-bet-00000011")
    km.bet("Shh", 50, 1.2, "crash-bet-00000012")
    draws.append(50.0)
    now[0] += K.BET_WINDOW_S + K.time_to(1.3)
    km.tick()
    pad, shh = db.get_bettor("Pad")["balance"], db.get_bettor("Shh")["balance"]
    with km.lock:
        km.void_open("test")
    assert db.get_bettor("Pad")["balance"] == pad + 25 and db.get_bettor("Shh")["balance"] == shh and km.view()["phase"] == "betting"
    assert db.query_one("SELECT status, payout FROM crash_bets WHERE request_id='crash-bet-00000011'") == {"status": "void", "payout": 25}
    assert K.CrashManager(db, clock=lambda: now[0]).table.round == 4  # a restart carries on counting rounds

    # The season's numbers, and the leaderboard's casino column: crash counts there and stays out of betting profit.
    season = km.season("Kra")
    assert season["rounds"] == 2 and season["cashed"] == 2 and season["best_mult"] == 100 and season["net"] == 100 + 495, season
    assert casino_nets(db)["kra"]["crash"] == 595 and casino_nets(db)["pad"]["crash"] == -25
    row = {r["name"]: r for r in bets.leaderboard()}["Kra"]
    assert row["casino"] == board["Kra"]["casino"] + 595 and abs(row["profit"] - board["Kra"]["profit"]) < 1e-9, row
    signed_out = km.view()
    assert signed_out["me"] is None and signed_out["stakes"] == K.STAKES and signed_out["edge"] == 0.03 and signed_out["growth"] == K.GROWTH


@section("poker")
def poker(shared):
    from unittest.mock import patch
    from fivestack import cards
    from fivestack.house import HouseManager, casino_nets
    from fivestack.poker import DEFAULTS, PokerManager, build_pots, check_settings, rake_for

    royal = cards.best_hand(["As", "Ks", "Qs", "Js", "Ts", "2d", "3c"])
    assert cards.describe(royal[0]) == "Royal flush" and sorted(royal[1]) == sorted(["As", "Ks", "Qs", "Js", "Ts"])
    assert cards.describe(cards.best_hand(["Ah", "2d", "3c", "4s", "5h", "Kd", "Kc"])[0]) == "Five-high straight"
    assert cards.describe(cards.best_hand(["Kh", "Kd", "Kc", "5s", "5h", "2d", "9c"])[0]) == "Kings full of Fives"
    assert cards.describe(cards.best_hand(["6h", "6d", "Kc", "Ks", "2h", "3d", "9c"])[0]) == "Two pair, Kings and Sixes"
    assert cards.best_hand(["Ah", "Ad", "Kc", "Qs", "2h"])[0] > cards.best_hand(["Kh", "Kd", "Ac", "Qs", "2h"])[0]
    assert build_pots({0: 50, 1: 200, 2: 200}, {0, 1, 2}) == [
        {"amount": 150, "eligible": [0, 1, 2]}, {"amount": 300, "eligible": [1, 2]}]
    assert build_pots({0: 100, 1: 100, 2: 40}, {0, 1}) == [{"amount": 240, "eligible": [0, 1]}]
    assert rake_for(100, True) == 1 and rake_for(1000, True) == 5 and rake_for(1000, False) == 0 and rake_for(99, True) == 0
    assert check_settings({"limit": "pot"}, DEFAULTS)["limit"] == "pot"
    for bad, msg in (({"min_buyin": 50}, "at least 10 big blinds"), ({"max_buyin": 2000}, "Buy-ins go"),
                     ({"small_blind": 3}, "Pick blinds"), ({"limit": "spread"}, "Pick no limit"),
                     ({"turn_seconds": 7}, "turn timer")):
        _expect_error(check_settings, bad, DEFAULTS, contains=msg)

    db = shared.casino_db
    start_total = sum(r["balance"] for r in db.query("SELECT balance FROM bettors"))
    clock = [5000.0]
    pk = PokerManager(db, clock=lambda: clock[0])
    bal = lambda n: db.get_bettor(n)["balance"]
    stack = lambda *cs: patch("fivestack.cards.shuffled", return_value=list(cs) + [c for c in cards.deck() if c not in cs])
    ace0, cy0 = bal("Ace"), bal("Cy")

    _expect_error(pk.sit, "Ace", 100, contains="Buy in for 200 to 1000")
    pk.sit("Ace", 500)
    pk.sit("Bea", 500)
    assert bal("Ace") == ace0 - 500 and db.query_one("SELECT stack FROM poker_seats WHERE bettor='Ace'")["stack"] == 500
    _expect_error(pk.sit, "Ace", 500, contains="already at the table")
    pk.set_ready("Ace", True)
    assert pk.view("Ace")["phase"] == "lobby"  # everyone has to ready up
    # Hand 1, heads-up: Ace has the button and the small blind and acts first. Bea's cards come first.
    with stack("2h", "3h", "7c", "8d", "Jc", "Kc", "Qd", "4s", "9s", "5d", "Ts", "6c"):
        v = pk.set_ready("Bea", True)
    assert v["phase"] == "playing" and v["hand"]["no"] == 1 and v["hand"]["pot"] == 15
    a = pk.view("Ace")
    assert a["me"]["legal"] == {"fold": True, "check": False, "call": 5, "raise": {"min": 20, "max": 500}, "verb": "Raise"}
    assert a["seats"][0]["cards"] == ["3h", "8d"] and a["seats"][1]["cards"] is None and a["seats"][1]["hidden"] == 2
    _expect_error(pk.update_settings, "Ace", {"limit": "pot"}, contains="once the game stops")
    _expect_error(pk.act, "Bea", "check", None, 1, 0, contains="not your turn")
    _expect_error(pk.act, "Ace", "raise", 15, 1, 0, contains="between 20 and 500")
    _expect_error(pk.act, "Ace", "call", None, 1, 3, contains="moved on")
    pk.act("Ace", "raise", 30, 1, 0)
    pk.act("Bea", "call", None, 1, 1)
    v = pk.view("Bea")
    assert v["hand"]["street"] == "flop" and v["hand"]["board"] == ["Kc", "Qd", "4s"] and v["hand"]["to_act"] == 1
    pk.act("Bea", "check", None, 1, 2)
    pk.act("Ace", "raise", 50, 1, 3)  # a bet of 50
    v = pk.act("Bea", "fold", None, 1, 4)
    # Ace's uncalled 50 comes back; no showdown, so no cards are shown. The pot was 60: 1% of it rounds down to 0.
    assert v["hand"]["street"] == "done" and v["last"]["pot"] == 60 and v["last"]["rake"] == 0 and not v["last"]["showdown"]
    assert [s["stack"] for s in v["seats"][:2]] == [530, 470] and v["seats"][0]["cards"] is None
    assert db.query("SELECT bettor, net FROM poker_results ORDER BY id") == [{"bettor": "Ace", "net": 30}, {"bettor": "Bea", "net": -30}]
    assert v["next_hand_at"] == 5006
    # Hand 2: the button moves to Bea. Aces against kings, checked down: the pot of 200 pays 2 to the house.
    with stack("As", "Kd", "Ah", "Kc", "5h", "2c", "7d", "9h", "6h", "3s", "8h", "4d"):
        pk.tick(5007)
    v = pk.view("Bea")
    assert v["hand"]["no"] == 2 and v["seats"][1]["button"] and v["seats"][1]["sb"] and v["hand"]["to_act"] == 1
    clock[0] = 5008
    pk.act("Bea", "raise", 100, 2, 0)
    pk.act("Ace", "call", None, 2, 1)
    step = 2
    for _ in range(3):  # flop, turn, river: Ace (out of position) then Bea
        pk.act("Ace", "check", None, 2, step)
        pk.act("Bea", "check", None, 2, step + 1)
        step += 2
    v = pk.view("Bea")
    assert v["last"]["showdown"] and v["last"]["rake"] == 2
    assert v["last"]["pots"] == [{"amount": 198, "winners": ["Ace"], "hand": "Pair of Aces"}]
    assert v["seats"][0]["cards"] == ["As", "Ah"] and v["seats"][1]["cards"] == ["Kd", "Kc"]  # both shown at showdown
    assert [s["stack"] for s in v["seats"][:2]] == [628, 370]
    # Cy sits down between hands and readies up: hand 3 is three-handed with Cy on the button.
    pk.sit("Cy", 200)
    pk.set_ready("Cy", True)
    with stack("Qh", "Kh", "Ah", "Qd", "Kd", "Ad", "5h", "2c", "7s", "9h", "6h", "3s", "8h", "4c"):
        pk.tick(5020)
    v = pk.view("Cy")
    assert v["hand"]["no"] == 3 and v["seats"][2]["button"] and v["seats"][0]["sb"] and v["seats"][1]["bb"]
    assert v["hand"]["to_act"] == 2
    clock[0] = 5021
    pk.act("Cy", "allin", None, 3, 0)          # 200
    pk.act("Ace", "allin", None, 3, 1)         # 628, a full raise
    v = pk.act("Bea", "call", None, 3, 2)      # all in for 370; the board runs out
    # Ace's uncalled 258 comes back. Main pot 600 (all three), side pot 340 (Ace and Bea); 5 credits of rake (the cap)
    # come out of the bigger pot. Cy's aces take the main pot, Bea's kings the side pot.
    last = v["last"]
    assert last["pot"] == 940 and last["rake"] == 5 and len(last["board"]) == 5
    assert last["pots"] == [{"amount": 595, "winners": ["Cy"], "hand": "Pair of Aces"},
                            {"amount": 340, "winners": ["Bea"], "hand": "Pair of Kings"}]
    assert [s["stack"] for s in v["seats"][:3]] == [258, 340, 595]
    assert sum(r["net"] for r in db.query("SELECT net FROM poker_results")) == -7  # what the house took, 2 + 5
    # Everyone un-readies: the game stops at the next deal, and the rules can change (un-readying everyone).
    for n in ("Ace", "Bea", "Cy"):
        pk.set_ready(n, False)
    pk.tick(5030)
    assert pk.view("Ace")["phase"] == "lobby" and pk.view("Ace")["hand"] is None
    pk.set_ready("Ace", True)
    v = pk.update_settings("Bea", {"limit": "pot", "small_blind": 10, "big_blind": 20})
    assert v["settings"]["limit"] == "pot" and not any(s and s["ready"] for s in v["seats"])
    assert db.get_meta("poker_settings")["big_blind"] == 20
    pk.leave("Cy")
    assert bal("Cy") == cy0 - 200 + 595
    # Pot limit: the small blind can raise to at most the pot after calling (20 + 30 + 10 = 60).
    with stack(*cards.deck()):
        pk.set_ready("Ace", True)
        pk.set_ready("Bea", True)
    v = pk.view("Ace")
    assert v["me"]["legal"]["raise"] == {"min": 40, "max": 60}
    _expect_error(pk.topup, "Ace", 100, contains="between hands")
    # A turn that times out checks if it can, else folds; two in a row sit you out.
    pk.tick(v["hand"]["deadline"] + 1)
    v = pk.view("Ace")
    assert v["last"]["no"] == v["hand"]["no"] and v["seats"][0]["timeouts"] == 1
    assert [e["kind"] for e in v["log"][-3:]] == ["timeout", "fold", "win"]
    with stack(*cards.deck()):
        pk.tick(v["next_hand_at"] + 1)
    v = pk.view("Bea")
    pk.act("Bea", "call", None, v["hand"]["no"], 0)
    pk.tick(pk.view("Ace")["hand"]["deadline"] + 1)  # Ace checks the option by timing out
    v = pk.view("Ace")
    assert v["seats"][0]["timeouts"] == 2 and not v["seats"][0]["ready"] and v["hand"]["street"] == "flop"
    # Leaving mid-hand folds and cashes out when the hand ends.
    pk.leave("Ace")
    v = pk.view("Bea")
    assert v["hand"]["street"] == "done" and v["seats"][0] is None
    # Top-ups between hands, up to the maximum buy-in.
    room = v["me"]["topup_room"]
    _expect_error(pk.topup, "Bea", room + 1, contains="up to")
    pk.topup("Bea", 50)
    assert v["me"]["stack"] + 50 == pk.view("Bea")["me"]["stack"]
    # A restart cashes out every seat at its stack from before the hand in play.
    seat_stack = db.query_one("SELECT stack FROM poker_seats WHERE bettor='Bea'")["stack"]
    before = bal("Bea")
    PokerManager(db, clock=lambda: clock[0])
    assert bal("Bea") == before + seat_stack and not db.query("SELECT * FROM poker_seats")
    # Nothing was created or lost: every credit is in a balance or the house's rake.
    rake = sum(r["rake"] for r in db.query("SELECT rake FROM poker_hands"))
    assert abs(sum(r["balance"] for r in db.query("SELECT balance FROM bettors")) + rake - start_total) < 1e-6
    nets = casino_nets(db)
    assert nets["cy"]["poker"] == 395 and nets["ace"]["total"] == nets["ace"]["blackjack"] + nets["ace"]["poker"]
    games = {g["game"]: g for g in HouseManager(db).summary()["games"]}
    assert games["poker"]["season"]["take"] == rake and games["poker"]["season"]["staked"] == 0
    # A season reset tags every casino row with the season and starts the casino column again.
    bets = BetManager({"starting_balance": 1000}, db, shared.engine)
    row = next(r for r in bets.leaderboard() if r["name"] == "Cy")
    assert row["casino"] == nets["cy"]["total"] and row["profit"] == 0
    bets.reset()
    assert casino_nets(db) == {} and HouseManager(db).summary()["season_take"] == 0
    assert db.query_one("SELECT COUNT(*) AS n FROM poker_results WHERE season_id IS NULL")["n"] == 0


@section("casino looks")
def casino_looks(shared):
    import types
    from fivestack.app import App
    from fivestack.bananas import GROUPS, ITEMS, SLOTS
    db = shared.casino_db
    bets = BetManager({"starting_balance": 1000}, db, shared.engine)
    bm = BananaManager({"banana_per_game": 5, "starting_bananas": 0}, db, bets)
    # Onkey's Casino is its own group in the shop, with its own slots and items.
    groups = {g[0] for g in GROUPS}
    assert groups == {"looks", "casino"} and all(slot[3] in groups for slot in SLOTS)
    casino_slots = {slot[0] for slot in SLOTS if slot[3] == "casino"}
    assert casino_slots == {"card_back", "chips", "seat", "entrance", "table_win"}
    assert all(any(i["slot"] == k for i in ITEMS.values()) for k in casino_slots)
    assert all("{name}" in i["look"]["text"] for i in ITEMS.values() if i["slot"] == "entrance")
    shop = bm.shop(db.get_bettor("Ace"))
    assert [g["key"] for g in shop["groups"]] == ["looks", "casino"]
    assert {x["key"]: x["group"] for x in shop["slots"]}["chips"] == "casino"
    # Buying casino items is bananas only, like the rest of the shop: worn at once, in everyone's looks.
    db.execute("INSERT INTO banana_ledger(bettor, delta, reason, ref, note, created_ts) VALUES('Ace', 4000, 'demo', 'casino-test', 'test', 0)")
    before = {r["name"]: (r["balance"], r["profit"]) for r in bets.leaderboard()}
    for item in ("cbk-gold", "chp-onyx", "st-neon", "en-royal", "tw-crown", "nc-gold"):
        bm.buy("Ace", item)
    assert {r["name"]: (r["balance"], r["profit"]) for r in bets.leaderboard()} == before
    worn = bm.looks()["ace"]["worn"]
    assert worn["card_back"]["cls"] == "cbk-gold" and worn["chips"]["cls"] == "chp-onyx" and worn["seat"]["cls"] == "st-neon"
    assert worn["entrance"]["text"].startswith("All rise") and worn["table_win"]["emoji"] == ["👑", "💎", "✨"]
    # Casino tables send the looks of everyone at them (seats, the log, the viewer), so other players see them.
    view = {"seats": [None, {"bettor": "Ace"}, {"bettor": "Bea"}], "log": [{"kind": "sit", "bettor": "Cy"}], "me": {"name": "Bea"}}
    out = App.with_looks(types.SimpleNamespace(bananas=bm), view)
    assert set(out["looks"]) == {"ace"} and out["looks"]["ace"]["worn"]["seat"]["cls"] == "st-neon"
    bm.equip("Ace", "seat", "")
    assert "seat" not in App.with_looks(types.SimpleNamespace(bananas=bm), view)["looks"]["ace"]["worn"]


@section("transfers")
def transfers(shared):
    db, bets = shared.db, shared.bets
    # Credits sent between bettors: zero-sum, and outside everyone's betting profit.
    before = {r["name"]: r for r in bets.leaderboard()}
    sent = bets.send("Tester", "p2", 30, "  side bet   on Bind  ")  # any case finds the account; the note is tidied
    assert sent["sender"] == "Tester" and sent["recipient"] == "P2" and sent["amount"] == 30 and sent["note"] == "side bet on Bind", sent
    bets.send("P2", "Tester", 12.345)  # cents are fine (rounded); no note
    after = {r["name"]: r for r in bets.leaderboard()}
    assert abs(after["Tester"]["balance"] - (before["Tester"]["balance"] - 30 + 12.35)) < 1e-6
    assert abs(after["P2"]["balance"] - (before["P2"]["balance"] + 30 - 12.35)) < 1e-6
    assert after["Tester"]["transfers"] == -17.65 and after["P2"]["transfers"] == 17.65, (after["Tester"], after["P2"])
    assert all(after[n]["profit"] == before[n]["profit"] for n in before), "transfers are not betting profit"
    assert sum(r["balance"] for r in after.values()) == sum(r["balance"] for r in before.values())  # nothing created
    assert [t["amount"] for t in db.transfers(received_by="tester")] == [12.35] and len(db.transfers(bettor="P2")) == 2
    assert db.transfers(received_by="P2")[0]["note"] == "side bet on Bind" and db.transfers(received_by="Tester")[0]["note"] is None
    tester_balance = db.get_bettor("Tester")["balance"]
    for bad in (("Tester", "Tester", 10, None, "yourself"), ("Tester", "Nobody", 10, None, "Pick who"),
                ("Tester", "", 10, None, "Pick who"), ("Tester", "P2", 0.5, None, "smallest"),
                ("Tester", "P2", "lots", None, "Enter an amount"), ("Tester", "P2", float("nan"), None, "smallest"),
                ("Tester", "P2", tester_balance + 1, None, "only has"), ("Tester", "P2", 5, "x" * 81, "80 characters"),
                ("Ghost", "P2", 5, None, "Sign in")):
        try:
            bets.send(*bad[:4])
            raise AssertionError(f"transfer should be refused: {bad}")
        except BetError as e:
            assert bad[4] in str(e), (bad, str(e))
    assert db.get_bettor("Tester")["balance"] == tester_balance and len(db.transfers()) == 2  # refusals change nothing
    assert db.transfer("Tester", "P2", tester_balance + 1, None) is None  # checked inside the transaction too
    assert all(t["tax_status"] is None for t in db.transfers())  # under 250 credits: no generosity tax
    assert tester_balance >= 250
    big = bets.send("Tester", "P2", tester_balance)  # the whole balance is allowed; 250+ earns the tax
    assert db.get_bettor("Tester")["balance"] == 0 and big["tax_status"] == "open", big
    assert bets.send("P2", "Tester", tester_balance)["tax_status"] == "open"  # P2 waits on Tester's next win

    # The generosity tax: Tester and Parlay each sent P2 250+ credits, so each takes 10% of the net winnings of P2's
    # next winning bet: the biggest, when several win on the same game. P2 pays it.
    assert bets.send("Tester", "P2", 60)["tax_status"] is None  # one open tax per sender and recipient: no stacking
    assert bets.send("Parlay", "P2", 249)["tax_status"] is None  # just under the minimum
    assert bets.send("Parlay", "P2", 250)["tax_status"] == "open"
    small = bets.place("P2", "team:win", "win", 10, {})
    large = bets.place("P2", "ou:kills:puuid-1", "over", 40, {})
    lost = bets.place("P2", "ou:kills:puuid-2", "over", 20, {})
    lb_before = {r["name"]: r for r in bets.leaderboard()}
    bal = {n: db.get_bettor(n)["balance"] for n in ("P2", "Tester", "Parlay")}
    game = {"match_id": "tax-game", "mode": "competitive", "rounds_won": 13, "rounds_lost": 5, "result": "win",
            "started_ts": time.time() + 5}  # settled on directly, never recorded, so later sections see no new game
    lines = [{**p, "kills": 30 if p["puuid"] == "puuid-1" else 5} for p in db.match_players("m9")]
    settled = {b["id"]: b for b in bets.settle_for_match(game, lines)}
    assert [settled[b["id"]]["status"] for b in (small, large, lost)] == ["won", "won", "lost"], settled
    net = {i: settled[i]["payout"] - settled[i]["stake"] for i in (small["id"], large["id"])}
    taxed = max(net, key=net.get)  # the kills over paid more
    tax = round(net[taxed] * 0.10, 2)
    assert taxed == large["id"] and "Generosity tax" in settled[taxed]["note"] and "Parlay" in settled[taxed]["note"]
    assert settled[small["id"]]["note"] is None  # only the biggest winner is taxed
    assert abs(db.get_bettor("Tester")["balance"] - (bal["Tester"] + tax)) < 1e-6
    assert abs(db.get_bettor("Parlay")["balance"] - (bal["Parlay"] + tax)) < 1e-6
    assert abs(db.get_bettor("P2")["balance"] - (bal["P2"] + sum(settled[i]["payout"] for i in net) - 2 * tax)) < 1e-6
    paid = [t for t in db.transfers(received_by="P2") if t["tax_status"] == "paid"]
    assert sorted(t["sender"] for t in paid) == ["Parlay", "Tester"] and all(t["tax_amount"] == tax and t["tax_bet_id"] == taxed
                                                                            and t["tax_match_id"] == "tax-game" for t in paid), paid
    assert paid[0]["tax_bet_net"] == net[taxed] and db.taxes_collected("tester")[0]["tax_amount"] == tax
    assert db.open_taxes("Tester", time.time() + 60)[0]["sender"] == "P2"  # Tester had no bets: still waiting
    lb_after = {r["name"]: r for r in bets.leaderboard()}
    assert abs(lb_after["P2"]["profit"] - (lb_before["P2"]["profit"] + sum(net.values()) - 20)) < 1e-6  # tax isn't betting
    assert lb_after["Tester"]["profit"] == lb_before["Tester"]["profit"]
    assert abs(lb_after["Tester"]["transfers"] - (lb_before["Tester"]["transfers"] + tax)) < 1e-6
    assert bets.collect_taxes(list(settled.values()), game) == []  # paid once only
    late = bets.send("Tester", "P2", 250)  # made after that game: waits for P2's next win
    assert late["tax_status"] == "open" and db.open_taxes("P2", late["created_ts"]) == []
    shared.transfer_count = len(db.transfers())


@section("bank")
def bank(shared):
    db, bets = shared.db, shared.bets
    bank = BankManager({"loan_max": 1000, "loan_interest": 0.1}, db)
    assert bank.terms() == {"max": 1000, "interest": 0.1, "min": 1, "enabled": True}
    name = "Tester"
    before = db.get_bettor(name)["balance"]
    board = {r["name"]: r for r in bets.leaderboard()}
    # Borrowing adds to the balance; the debt carries interest; betting profit doesn't move.
    loan = bank.borrow(name, 400)
    assert (loan["principal"], loan["interest"], loan["owed"], loan["repaid"], loan["cleared_ts"]) == (400, 40, 440, 0, None), loan
    assert abs(db.get_bettor(name)["balance"] - (before + 400)) < 1e-9
    st = bank.status(name)
    assert (st["borrowed"], st["debt"], st["room"], len(st["open"]), st["open"][0]["due"], st["cleared"]) == (400, 440, 600, 1, 440, [])
    row = {r["name"]: r for r in bets.leaderboard()}[name]
    assert row["debt"] == 440 and row["borrowed"] == 400 and abs(row["profit"] - board[name]["profit"]) < 1e-9, (row, board[name])
    assert abs(row["balance"] - (before + 400)) < 1e-9
    # The limit is on what's out: 600 more at most, then nothing until it's all paid back with interest.
    for amount, msg in [(601, "lend you 600 more"), (0, "smallest"), ("x", "Enter"), (10.5, "Whole credits"), (float("nan"), "smallest")]:
        try:
            bank.borrow(name, amount)
        except BetError as e:
            assert msg in str(e), (amount, e)
        else:
            raise AssertionError(f"borrow {amount} should fail")
    second = bank.borrow(name, 600)
    st = bank.status(name)
    assert (st["borrowed"], st["debt"], st["room"], len(st["open"])) == (1000, 1100, 0, 2)
    try:
        bank.borrow(name, 1)
    except BetError as e:
        assert "full 1000" in str(e), e
    else:
        raise AssertionError("borrowing past the limit should fail")
    for bad in [("Nobody", 10), (name, 10)]:
        try:
            BankManager({"loan_max": 0}, db).borrow(*bad) if bad[0] == name else bank.borrow(*bad)
        except BetError as e:
            assert ("Sign in" in str(e)) == (bad[0] == "Nobody"), (bad, e)
        else:
            raise AssertionError(f"borrow {bad} should fail")
    # Paying back: any whole amount, oldest loan first, never more than the balance or the debt.
    bal = db.get_bettor(name)["balance"]
    assert bank.repay(name, 100) == {"paid": 100, "cleared": []} and abs(db.get_bettor(name)["balance"] - (bal - 100)) < 1e-9
    st = bank.status(name)
    assert st["debt"] == 1000 and st["room"] == 0 and {l["id"]: l["repaid"] for l in st["open"]} == {loan["id"]: 100, second["id"]: 0}
    r = bank.repay(name, 340)  # clears the first loan to the credit
    assert r == {"paid": 340, "cleared": [loan["id"]]}, r
    st = bank.status(name)
    assert (st["borrowed"], st["debt"], st["room"], len(st["open"]), len(st["cleared"])) == (600, 660, 400, 1, 1)
    assert st["cleared"][0]["id"] == loan["id"] and st["cleared"][0]["repaid"] == 440 and st["cleared"][0]["cleared_ts"]
    db.execute("UPDATE bettors SET balance = 50 WHERE name=?", (name,))
    try:
        bank.repay(name, 60)
    except BetError as e:
        assert "only have 50" in str(e), e
    else:
        raise AssertionError("paying more than the balance should fail")
    assert bank.status(name)["debt"] == 660  # nothing moved
    db.execute("UPDATE bettors SET balance = 5000 WHERE name=?", (name,))
    r = bank.repay(name, 9999)  # more than the debt pays just the debt; repay(name) with no amount does the same
    assert r == {"paid": 660, "cleared": [second["id"]]} and abs(db.get_bettor(name)["balance"] - (5000 - 660)) < 1e-9, r
    st = bank.status(name)
    assert (st["borrowed"], st["debt"], st["room"], st["open"], len(st["cleared"])) == (0, 0, 1000, [], 2)
    try:
        bank.repay(name)
    except BetError as e:
        assert "don't owe" in str(e), e
    else:
        raise AssertionError("repaying with no debt should fail")
    # Interest paid isn't a betting loss and credits on loan aren't profit: with the balance back where the loans
    # left it (100 interest paid in all), profit is what it was before any of this.
    db.execute("UPDATE bettors SET balance = ? WHERE name=?", (before - 100, name))
    row = {r["name"]: r for r in bets.leaderboard()}[name]
    assert abs(row["profit"] - board[name]["profit"]) < 1e-9 and row["debt"] == 0, (row, board[name])
    assert db.loan_totals()[name.lower()] == {"k": name.lower(), "borrowed": 0, "debt": 0, "net": -100}
    # One loan left open for the season reset to archive; the rankings put the debt against the balance.
    bank.borrow(name, 250)
    ranked = bets.leaderboard()
    assert [r["name"] for r in ranked] == [r["name"] for r in sorted(ranked, key=lambda r: -(r["balance"] - r["debt"]))]
    assert {r["name"]: r["debt"] for r in ranked}[name] == 275
    shared.loan_count = len(db.loans())
    assert shared.loan_count == 3 and len(db.loans(name, open_only=True)) == 1


@section("banana hunt")
def banana_hunt(shared):
    from fivestack.wheel import next_reset, wheel_day

    db, bets = shared.db, shared.bets
    hunt = HuntManager(db, {"hunt_daily_max": 5, "hunt_floor": 250}, extras=False)  # the plain hunt: one banana, a credit each
    assert hunt.terms()["daily_max"] == 5 and hunt.terms()["floor"] == 250 and hunt.terms()["per_banana"] == 1 and hunt.enabled
    name = "Tester"
    db.execute("UPDATE bettors SET balance = 1000 WHERE name=?", (name,))  # well above the floor, so the cap applies
    before = db.get_bettor(name)["balance"]
    board = {r["name"]: r for r in bets.leaderboard()}
    t0 = 1_800_000_000.0  # a fixed "now", a Pacific morning in 2027
    st = hunt.start(name, now=t0)
    tgt = st["target"]
    assert st["today"] == 0 and st["left"] == 5 and not st["done"] and st["day"] == wheel_day(t0) and st["resets_ts"] == next_reset(t0)
    assert not st["under_floor"] and st["balance"] == before and before >= 250  # well above the floor: the cap applies
    assert MARGIN <= tgt["x"] <= FIELD_W - MARGIN and MARGIN <= tgt["y"] <= FIELD_H - MARGIN, tgt
    # A miss pays nothing and leaves the banana where it is.
    r = hunt.click(name, tgt["x"] + TARGET_R + 2, tgt["y"], now=t0 + 1)
    assert r["hit"] is False and r["reason"] == "miss" and r["target"] == tgt and db.get_bettor(name)["balance"] == before, r
    # A hit pays one credit and moves the banana at least a hop away.
    r = hunt.click(name, tgt["x"] + 3, tgt["y"] - 3, now=t0 + 1.5)
    assert r["hit"] and r["paid"] == 1 and r["today"] == 1 and r["left"] == 4 and db.get_bettor(name)["balance"] == before + 1, r
    new = r["target"]
    assert new != tgt and math.hypot(new["x"] - tgt["x"], new["y"] - tgt["y"]) >= MIN_HOP, (tgt, new)
    assert hunt.status(name, now=t0 + 1.5)["target"] == new
    # Too soon after the last paid pick: not paid, and the banana stays put.
    r = hunt.click(name, new["x"], new["y"], now=t0 + 1.6)
    assert r["hit"] is False and r["reason"] == "too_fast" and r["target"] == new and db.get_bettor(name)["balance"] == before + 1, r
    # Up to the day's cap, then done until the next Pacific day.
    t = t0 + 2.2  # past the pace limit since the last paid pick
    for _ in range(4):
        r = hunt.click(name, r["target"]["x"], r["target"]["y"], now=t)
        assert r["hit"] and not r["under_floor"], r
        t += 0.7
    assert r["left"] == 0 and r["done"] and r["target"] is None and r["today"] == 5
    r = hunt.click(name, 100, 100, now=t)
    assert r["hit"] is False and r["reason"] == "done" and r["target"] is None
    st = hunt.status(name, now=t)
    assert st["today"] == 5 and st["done"] and st["target"] is None and db.get_bettor(name)["balance"] == before + 5
    assert hunt.start(name, now=t)["target"] is None  # starting again doesn't help
    # Under the floor (250 in this manager; 50 by default, checked below) the cap doesn't apply: with 247 credits,
    # three more picks are allowed, then it's done again.
    db.execute("UPDATE bettors SET balance = 247 WHERE name=?", (name,))
    st = hunt.start(name, now=t)
    assert st["under_floor"] and st["left"] == 3 and not st["done"] and st["target"], st
    for i in range(3):
        r = hunt.click(name, st["target"]["x"], st["target"]["y"], now=t + 1 + i)
        assert r["hit"] and r["today"] == 6 + i, r
        st["target"] = r["target"]
    assert r["done"] and r["target"] is None and not r["under_floor"] and db.get_bettor(name)["balance"] == 250, r
    assert hunt.click(name, 100, 100, now=t + 5)["reason"] == "done" and hunt.status(name, now=t + 5)["today"] == 8
    db.execute("UPDATE bettors SET balance = 249.5 WHERE name=?", (name,))  # half a credit short: one more pick
    assert hunt.start(name, now=t + 6)["left"] == 1
    db.execute("UPDATE bettors SET balance = ? WHERE name=?", (before + 8, name))  # the 8 picks so far, as if nothing else moved
    nxt = next_reset(t) + 1
    st = hunt.start(name, now=nxt)
    assert st["today"] == 0 and st["left"] == 5 and st["target"], st
    r = hunt.click(name, st["target"]["x"], st["target"]["y"], now=nxt + 1)
    assert r["hit"] and db.get_bettor(name)["balance"] == before + 9 and hunt.today(name, now=nxt) == 1 and r["left"] == 4
    # Bad input, nobody, and a closed hunt.
    for bad in [("x", 1), (None, 2), (float("nan"), 3)]:
        try:
            hunt.click(name, *bad, now=nxt + 2)
        except BetError as e:
            assert "click" in str(e), e
        else:
            raise AssertionError(f"click {bad} should fail")
    for fn in (lambda: hunt.click("Nobody", 1, 1), lambda: hunt.start("Nobody"),
               lambda: HuntManager(db, {"hunt_daily_max": 0}).click(name, 1, 1)):
        try:
            fn()
        except BetError:
            pass
        else:
            raise AssertionError("should fail")
    assert not HuntManager(db, {"hunt_daily_max": 0}).enabled
    # The leaderboard reports the hunt in its own column and keeps it out of profit; the board ranks pickers.
    row = {r["name"]: r for r in bets.leaderboard()}[name]
    assert row["hunt"] == 9 and abs(row["profit"] - board[name]["profit"]) < 1e-9, (row, board[name])
    top = hunt.board()[0]
    assert top["name"] == name and top["season"] == 9 and top["all_time"] == 9 and top["bananas"] == 9, top
    assert db.hunt_totals()[name.lower()]["season"] == 9 and len(db.query("SELECT 1 FROM hunt_days")) == 2  # two days
    assert hunt.summary(db.get_bettor(name), now=nxt + 2)["me"]["today"] == 1 and hunt.summary()["me"] is None
    shared.hunt_rows = 2

    # ---- the extras: what Onkey throws, catches in the air, the combo, Greg, the hidden item ----
    import random as _random
    from fivestack import hunt as H

    class Rig:  # the manager's dice: `rolls` decide what's thrown next (0.99, a plain banana, once they run out)
        def __init__(self):
            self.rolls = []

        def random(self):
            return self.rolls.pop(0) if self.rolls else 0.99

        def randint(self, a, b):
            return _random.randint(a, b)

    GOLD, BUNCH, ROTTEN, GREG = 0.01, 0.06, 0.15, 0.25
    hx = HuntManager(db, {"hunt_daily_max": 500, "hunt_floor": 250})
    rig = hx.rng = Rig()
    who = "Hunter"
    bets.register(who, "secret1")
    db.execute("UPDATE bettors SET balance = 1000 WHERE name=?", (who,))
    start_balance = db.get_bettor(who)["balance"]
    now = 1_900_000_000.0
    day = wheel_day(now)
    assert hx.terms(now)["theme"] == H.theme(day) and H.theme(day) in H.THEMES and hx.terms(now)["extras"]
    assert len({H.theme(wheel_day(now + d * 86400)) for d in range(40)}) > 1  # the field changes by the day
    # No streaks: picking on the days before changes nothing. The first banana pays one, and the cap is the cap.
    for back in (1, 2):
        db.execute("INSERT INTO hunt_days(bettor, day, bananas, credits, updated_ts) VALUES(?,?,?,?,?)",
                   (who, wheel_day(now - back * 86400), 4, 4.0, now - back * 86400))
    st = hx.start(who, now=now)
    assert "streak" not in st and "cap" not in st and st["left"] == 500 and st["target"]["kind"] == "banana", st
    r = hx.click(who, st["target"]["x"], st["target"]["y"], now=now + 1)
    assert r["hit"] and r["paid"] == 1 and "streak_bonus" not in r and r["combo"] == 1 and r["mult"] == 1 and r["today"] == 1, r
    # The same banana isn't swapped by starting again (no fishing for a golden one).
    tgt = r["target"]
    assert hx.start(who, now=now + 1.2)["target"] == tgt
    # Caught in the air: a click on the arc pays double; a click off the arc is a miss and breaks the combo.
    ax, ay = H.arc_at(tgt["x"], tgt["y"], 0.5)
    r = hx.click(who, ax + 5, ay - 5, now=now + 1.6, air=0.5)
    assert r["hit"] and r["air"] and r["paid"] == 2 and r["combo"] == 2, r
    tgt = r["target"]
    ax, ay = H.arc_at(tgt["x"], tgt["y"], 0.5)
    r = hx.click(who, ax + H.AIR_R + 30, ay, now=now + 2.0, air=0.5)
    assert not r["hit"] and r["reason"] == "miss" and r["combo"] == 0 and r["target"] == tgt, r
    now += 3
    # A golden banana: five credits on the ground, and it rots if it's left.
    rig.rolls = [GOLD]
    r = hx.click(who, tgt["x"], tgt["y"], now=now)
    assert r["hit"] and r["paid"] == 1 and r["target"]["kind"] == "golden", r
    gold = r["target"]
    r = hx.click(who, gold["x"], gold["y"], now=now + 1)
    assert r["hit"] and r["kind"] == "golden" and r["paid"] == H.GOLD_VALUE, r
    rig.rolls = [GOLD]
    r = hx.click(who, r["target"]["x"], r["target"]["y"], now=now + 2)
    gold = r["target"]
    assert gold["kind"] == "golden" and hx.nudge(who, now=now + 2.5)["reason"] is None  # still good: nothing to swap
    late = now + 2 + H.AIR_S + H.GOLD_TTL_S + H.SLACK_S + 0.1
    r = hx.click(who, gold["x"], gold["y"], now=late)
    assert not r["hit"] and r["reason"] == "rotted" and r["target"]["id"] != gold["id"] and r["combo"] == 3, r
    now = late + 1
    # A rotten decoy beside the banana: picking it freezes the bettor and breaks the combo.
    rig.rolls = [ROTTEN]
    r = hx.click(who, r["target"]["x"], r["target"]["y"], now=now)
    both = r["target"]
    assert r["hit"] and both["decoy"] and math.hypot(both["decoy"]["x"] - both["x"], both["decoy"]["y"] - both["y"]) >= H.DECOY_GAP, r
    r = hx.click(who, both["decoy"]["x"], both["decoy"]["y"], now=now + 1)
    assert not r["hit"] and r["reason"] == "rotten" and r["combo"] == 0 and r["frozen_s"] == H.FREEZE_S and r["target"]["id"] != both["id"], r
    tgt = r["target"]
    r = hx.click(who, tgt["x"], tgt["y"], now=now + 2)
    assert not r["hit"] and r["reason"] == "frozen", r
    now += 1 + H.FREEZE_S + 0.1
    # Greg walks to the banana: shoo him, or he takes it and the combo with it.
    rig.rolls = [GREG]
    r = hx.click(who, tgt["x"], tgt["y"], now=now)
    assert r["hit"] and r["target"]["greg"], r
    tgt = r["target"]
    r = hx.click(who, 0, 0, now=now + 0.5, shoo=True)
    assert not r["hit"] and r["reason"] == "shooed" and "greg" not in r["target"] and r["target"]["id"] == tgt["id"] and r["combo"] == 1, r
    rig.rolls = [GREG]
    r = hx.click(who, tgt["x"], tgt["y"], now=now + 1)
    tgt = r["target"]
    assert r["hit"] and tgt["greg"] and r["combo"] == 2
    gone = now + 1 + H.AIR_S + H.GREG_S + 0.1  # the page's own timer: the server's has SLACK_S more
    r = hx.nudge(who, now=gone)
    assert r["reason"] == "stolen" and r["combo"] == 0 and r["target"]["id"] != tgt["id"], r
    now = gone + 1
    # From the lore: the scientist's claw takes a banana that's left (it can't be shooed), and Man Strudel only visits.
    rig.rolls = [0.33]
    r = hx.click(who, r["target"]["x"], r["target"]["y"], now=now)
    tgt = r["target"]
    assert r["hit"] and tgt["claw"] is True and "greg" not in tgt, r
    assert hx.click(who, 0, 0, now=now + 0.5, shoo=True)["reason"] == "miss"  # nobody to shoo: the claw stays
    taken = hx.nudge(who, now=now + H.AIR_S + H.CLAW_S + 0.1)
    assert taken["reason"] == "clawed" and taken["combo"] == 0 and taken["target"]["id"] != tgt["id"], taken
    now += H.AIR_S + H.CLAW_S + 1
    rig.rolls = [0.39]
    r = hx.click(who, taken["target"]["x"], taken["target"]["y"], now=now)
    tgt = r["target"]
    assert r["hit"] and tgt["strudel"] and "claw" not in tgt, r
    r = hx.click(who, tgt["x"], tgt["y"], now=now + 1)
    assert r["hit"] and r["paid"] == 1 and hx.nudge(who, now=now + 1.5)["reason"] is None, r  # he takes nothing
    now += 2
    # A bunch: five at once, a credit each, and a bonus for sweeping them all in time.
    rig.rolls = [BUNCH]
    r = hx.click(who, r["target"]["x"], r["target"]["y"], now=now)
    bunch = r["target"]
    assert r["hit"] and bunch["kind"] == "bunch" and len(bunch["items"]) == H.BUNCH_SIZE, r
    paid = []
    for n, item in enumerate(bunch["items"]):
        r = hx.click(who, item["x"], item["y"], now=now + 1 + n * 0.2)
        assert r["hit"] and r["kind"] == "bunch", r
        paid.append(r["paid"])
    assert paid == [1, 1, 1, 1, 1 + H.BUNCH_BONUS] and r["swept"] and r["bunch_bonus"] == H.BUNCH_BONUS and r["target"]["kind"] == "banana", (paid, r)
    now += 3
    # The combo: the tenth pick in a row pays double.
    while r["combo"] < H.COMBO_STEPS[0] - 1:
        r = hx.click(who, r["target"]["x"], r["target"]["y"], now=now)
        assert r["hit"] and r["paid"] == 1, r
        now += 0.7
    r = hx.click(who, r["target"]["x"], r["target"]["y"], now=now)
    assert r["hit"] and r["combo"] == H.COMBO_STEPS[0] and r["mult"] == 2 and r["paid"] == 2, r
    assert hx.status(who, now=now + H.COMBO_IDLE_S + 1)["combo"] == 0  # and it lapses if they stop
    # The hidden item: one of the day's picks, the same one however often it's asked, handed over once.
    nth, prize = hx._hidden(who, day)
    assert (nth, prize) == hx._hidden(who, day) and H.HIDDEN_FROM <= nth <= H.HIDDEN_TO and prize in ("bananas", "boost", "insurance")
    now += H.COMBO_IDLE_S + 2
    while hx.status(who, now=now)["picks"] < H.HIDDEN_TO + 1:
        r = hx.click(who, r["target"]["x"], r["target"]["y"], now=now)
        assert r["hit"], r
        now += 0.7
    ledger = db.query("SELECT delta FROM banana_ledger WHERE reason='hunt' AND bettor=?", (who,))
    assert len(ledger) == 1 and ledger[0]["delta"] == (H.HIDDEN_BANANAS if prize == "bananas" else 0), (ledger, prize)
    assert len(db.query("SELECT 1 FROM wheel_perks WHERE bettor=? AND kind=?", (who, prize))) == (prize != "bananas")
    assert hx._found(who, day, nth, now) is None  # never twice
    # Everything paid is in the day's row and the balance.
    st = hx.status(who, now=now)
    assert st["today"] == round(db.get_bettor(who)["balance"] - start_balance) and st["left"] == 500 - st["today"], st
    # The cap counts credits: a golden banana can't pay past what's left.
    tight = HuntManager(db, {"hunt_daily_max": st["today"] + 2, "hunt_floor": 0})
    tight.rng = Rig()
    tight.rng.rolls = [GOLD]
    st = tight.start(who, now=now + 1)
    assert st["left"] == 2 and st["target"]["kind"] == "golden", st  # two under the cap
    r = tight.click(who, st["target"]["x"], st["target"]["y"], now=now + 2)
    assert r["hit"] and r["paid"] == 2 and r["done"] and r["target"] is None, r
    # Past the cap there's only the top-up: by default, back up to 50 credits and no further.
    from fivestack.hunt import FLOOR
    topup = HuntManager(db, {"hunt_daily_max": 1})  # well past this cap already
    topup.rng = Rig()
    assert FLOOR == 50 and topup.floor == 50 and topup.terms()["floor"] == 50
    assert topup.start(who, now=now + 3)["done"]  # plenty of credits: closed for the day
    db.execute("UPDATE bettors SET balance = 47 WHERE name=?", (who,))
    st = topup.start(who, now=now + 3)
    assert st["under_floor"] and st["left"] == 3 and not st["done"], st
    for n in range(3):
        r = topup.click(who, st["target"]["x"], st["target"]["y"], now=now + 4 + n)
        assert r["hit"] and r["paid"] == 1, r
        st["target"] = r["target"]
    assert r["done"] and r["target"] is None and db.get_bettor(who)["balance"] == 50, r
    db.execute("UPDATE bettors SET balance = 1000 WHERE name=?", (who,))
    shared.hunt_rows = db.query_one("SELECT COUNT(*) AS n FROM hunt_days")["n"]


@section("seasons")
def seasons(shared):
    db, bets = shared.db, shared.bets
    # Resetting the season archives it first: final standings, every bet and every reward.
    before = {"bets": len(db.bets()), "rewards": len(db.rewards()), "open": sum(b["status"] == "pending" for b in db.bets()),
              "standings": {r["name"]: r for r in bets.leaderboard()}, "house": bets.house()}
    assert db.season_counts()["bets"] == before["bets"] and db.seasons() == []
    season = bets.reset()
    assert bets.house() == before["house"] and before["house"]["bets"] > 0  # archived bets still count for the house
    assert db.rewards() == [] and db.reward_totals() == {} and db.get_bettor("P2")["balance"] == 1000 and db.bets() == []
    assert season["name"] == "Season 1" and season["bets"] == before["bets"] and season["rewards"] == before["rewards"]
    saved = {r["name"]: r for r in season["standings"]}
    assert saved["Tester"]["balance"] == before["standings"]["Tester"]["balance"] and saved["P2"]["rewards"] == 750
    archived = db.archived_bets(season["id"])
    assert len(archived) == before["bets"] and not any(b["status"] == "pending" for b in archived)
    assert sum(b["note"] == "Still open when the season was reset" for b in archived) == before["open"]
    assert db.query_one("SELECT COUNT(*) AS n FROM archived_rewards WHERE season_id=?", (season["id"],))["n"] == before["rewards"]
    assert db.query_one("SELECT COUNT(*) AS n FROM archived_transfers WHERE season_id=?", (season["id"],))["n"] == shared.transfer_count
    assert db.transfers() == [] and db.transfer_totals() == {}
    # Loans are archived and the debts forgiven with the balances.
    assert db.query_one("SELECT COUNT(*) AS n FROM archived_loans WHERE season_id=?", (season["id"],))["n"] == shared.loan_count
    assert db.loans() == [] and db.loan_totals() == {} and all(r["debt"] == 0 for r in bets.leaderboard())
    # The hunt's day rows are tagged with the season; this season's column starts from zero.
    assert db.query_one("SELECT COUNT(*) AS n FROM hunt_days WHERE season_id=?", (season["id"],))["n"] == shared.hunt_rows
    assert all(r["hunt"] == 0 for r in bets.leaderboard()) and db.hunt_totals()["tester"]["all_time"] == 9
    assert saved["P2"]["transfers"] == before["standings"]["P2"]["transfers"] != 0  # the standings keep them
    assert db.season_counts() == {"started_ts": season["ended_ts"], "bets": 0, "rewards": 0, "transfers": 0,
                                  "bettors": db.season_counts()["bettors"]}
    assert bets.reset()["name"] == "Season 2" and [x["name"] for x in db.seasons()] == ["Season 2", "Season 1"]
    # Bananas went back to zero with the credits; bought items stay, and nothing is paid again for the old season.
    assert all(abs(t["wallet"]) < 1e-9 and t["season_earned"] == 0 for t in db.banana_totals().values())
    assert len(db.banana_items("Tester")) == 2 and shared.bananas.earn() == 0
    assert shared.starter.earn() == len(db.bettors()) and all(abs(db.banana_wallet(b["name"]) - 50) < 1e-9 for b in db.bettors())  # a fresh 50


@section("forfeits")
def forfeits(shared):
    bets = shared.bets
    # Forfeits (surrenders) and no-contest games.
    comp = lambda rw, rl, mode="competitive": {"mode": mode, "rounds_won": rw, "rounds_lost": rl}  # noqa: E731
    assert [ending(comp(*x)) for x in [(13, 9), (14, 12), (9, 4), (4, 9), (2, 1), (3, 1)]] == [COMPLETE, COMPLETE, FORFEIT, FORFEIT, NO_CONTEST, NO_CONTEST]
    assert ending(comp(5, 3, "swiftplay")) == COMPLETE and ending(comp(4, 3, "swiftplay")) == FORFEIT
    assert ending({"rounds_won": None, "rounds_lost": None}) == COMPLETE
    assert full_game_rounds([comp(13, 9)] * 4) == 22.0  # too few games: default
    assert full_game_rounds([comp(13, 5), comp(13, 7), comp(13, 11), comp(14, 12), comp(9, 4), comp(13, 9)]) == 22.0  # median, forfeit ignored

    # The full record's winner flag decides a surrender, even when the team that gave up led on rounds.
    rec = {"metadata": {"match_id": "ff", "map": {"name": "Bind"}, "queue": {"id": "competitive"}, "started_at": "2026-09-22T18:00:00Z"},
           "players": [v4_player(p, "Blue") for p in PUUIDS.values()],
           "teams": [{"team_id": "Blue", "rounds": {"won": 5, "lost": 7}, "won": True},
                     {"team_id": "Red", "rounds": {"won": 7, "lost": 5}, "won": False}]}
    assert parse_details(rec, set(PUUIDS.values()))[0]["result"] == "win"

    # Settlement on a 9-4 surrender (13 rounds played): only already-decided markets settle.
    ff = {"match_id": "ff", "mode": "competitive", "rounds_won": 9, "rounds_lost": 4, "result": "win"}
    ff_metrics = {"puuid-1": {"kills": 12, "deaths": 5, "assists": 3, "acs": 300.0, "adr": 190.0, "hs_pct": 30.0},
                  "puuid-2": {"kills": 8, "deaths": 7, "assists": 2, "acs": 200.0, "adr": 130.0, "hs_pct": 20.0}}

    def settle(mtype, sel, line=None, **ctx):
        st, _, note = bets._evaluate({"market_type": mtype, "selection": sel, "line": line, "context": json.dumps(ctx)}, ff, ff_metrics)
        return st if st != "void" else f"void: {note[:24]}"
    assert settle("team_win", "win") == "won" and settle("team_win", "loss") == "lost"  # a surrender is an official result
    assert settle("team_ou", "over", 12.5) == "won" and settle("team_ou", "under", 12.5) == "lost"  # 13 already > 12.5
    assert settle("team_ou", "over", 21.5).startswith("void") and settle("team_ou", "under", 21.5).startswith("void")
    assert settle("ou", "over", 10.5, stat="kills", puuid="puuid-1") == "won"  # 12 kills already cleared 10.5
    assert settle("ou", "under", 10.5, stat="kills", puuid="puuid-1") == "lost"
    assert settle("ou", "over", 10.5, stat="kills", puuid="puuid-2").startswith("void")  # 8 kills: undecided
    assert settle("ou", "under", 10.5, stat="kills", puuid="puuid-2").startswith("void")
    assert settle("ou", "over", 150.5, stat="acs", puuid="puuid-1").startswith("void")  # rates are never decided early
    assert settle("top", "puuid-1", stat="kills").startswith("void")
    # Parlays on a surrender: every leg uses the same rules. An undecided leg is dropped (not refunded) and the
    # payout uses the legs that stood; a leg already decided against the bettor still loses the whole parlay.
    def parlay(*legs):
        built = [{"market_type": t, "selection": sel, "line": line, "odds_decimal": 2.0, "meta": meta, "description": t}
                 for t, sel, line, meta in legs]
        return bets._evaluate_parlay({"stake": 10.0, "odds_decimal": 2.0 ** len(built),
                                      "context": json.dumps({"legs": built})}, ff, ff_metrics)
    status, payout, _, note, ctx_json = parlay(("team_win", "win", None, {}),
                                               ("ou", "over", 10.5, {"stat": "kills", "puuid": "puuid-1"}),  # 12: decided
                                               ("ou", "over", 150.5, {"stat": "acs", "puuid": "puuid-1"}))   # rate: dropped
    assert status == "won" and payout == 40.0 and "surrender" in note, (status, payout, note)  # 10 x 2.0 x 2.0
    assert [leg["result"] for leg in json.loads(ctx_json)["legs"]] == ["won", "won", "void"]
    assert parlay(("team_win", "win", None, {}), ("ou", "over", 10.5, {"stat": "kills", "puuid": "puuid-2"}),  # undecided
                  ("team_ou", "under", 12.5, {}))[0] == "lost"  # 13 rounds already beat the under
    status, payout, _, note, _ = parlay(("ou", "over", 150.5, {"stat": "acs", "puuid": "puuid-1"}), ("top", "puuid-1", None, {"stat": "kills"}))
    assert status == "void" and payout == 10.0 and "surrender" in note, (status, payout, note)  # nothing decided: refunded
    # The legs that stood are re-priced together from their saved histories: two legs that always landed together
    # pay 2.33, not 2.0 x 2.0 (see "parlays").
    together = "1" * 15 + "0" * 15
    linked = [{"market_type": "team_win", "selection": "win", "line": None, "odds_decimal": 2.0, "meta": {}, "hist": together},
              {"market_type": "ou", "selection": "over", "line": 10.5, "odds_decimal": 2.0, "hist": together,
               "meta": {"stat": "kills", "puuid": "puuid-1"}},
              {"market_type": "ou", "selection": "over", "line": 150.5, "odds_decimal": 2.0, "hist": "10" * 15,
               "meta": {"stat": "acs", "puuid": "puuid-1"}}]  # voided by the surrender
    status, payout, *_ = bets._evaluate_parlay({"stake": 10.0, "odds_decimal": 5.0, "context": json.dumps({"legs": linked})}, ff, ff_metrics)
    assert status == "won" and payout == 23.3, (status, payout)
    # Overtime: a completed game settles on whether it reached 12-12; a surrender only if it already had.
    def ot_bet(sel, match):
        return bets._evaluate({"market_type": "team_ot", "selection": sel, "line": None, "context": "{}"}, match, ff_metrics)[0]
    assert ot_bet("yes", dict(ff, rounds_won=14, rounds_lost=12)) == "won" and ot_bet("no", dict(ff, rounds_won=14, rounds_lost=12)) == "lost"
    assert ot_bet("no", dict(ff, rounds_won=13, rounds_lost=11)) == "won"
    assert ot_bet("yes", ff) == "void" and ot_bet("no", ff) == "void"  # 9-4 surrender: undecided
    assert ot_bet("yes", dict(ff, rounds_won=12, rounds_lost=12)) == "won"  # surrendered at 12-12: OT was reached
    done = dict(ff, rounds_won=13, rounds_lost=4)  # the same numbers in a completed game settle normally
    assert bets._evaluate({"market_type": "ou", "selection": "under", "line": 10.5,
                           "context": json.dumps({"stat": "kills", "puuid": "puuid-2"})}, done, ff_metrics)[0] == "won"


@section("forfeit odds")
def forfeit_odds(shared):
    # Odds: a forfeit is scaled to a full-length game at reduced weight, and kept out of the rounds market.
    scaled, share = partial_game({"kills": 10, "deaths": 6, "assists": 2, "acs": 250.0}, 11, 22.0)
    assert share == 0.5 and scaled["kills"] == 20 and scaled["deaths"] == 12 and scaled["acs"] == 250.0
    assert partial_game({"kills": 10}, 24, 22.0) == ({"kills": 10}, 1.0)
    odb = DB(os.path.join(shared.tmp, "odds.db"))
    for i, pu in enumerate(PUUIDS.values()):
        odb.upsert_member(pu, f"P{i + 1}", "TAG", "na", "", None, i)
    for k in range(7):  # six full 13-11 games (24 rounds, 20 kills each) and one 8-4 surrender with 10 kills
        rw, rl = (8, 4) if k == 3 else (13, 11)
        odb.insert_match({"match_id": f"o{k}", "map": "Ascent", "mode": "competitive", "started_ts": 1000 + k,
                          "rounds_won": rw, "rounds_lost": rl, "result": "win"},
                         [{"puuid": pu, "agent": "Jett", "kills": 10 if k == 3 else 20, "deaths": 15, "assists": 4,
                           "score": 4800, "damage_dealt": 3000, "headshots": 5, "bodyshots": 20, "legshots": 1} for pu in PUUIDS.values()])
    ob = OddsEngine(shared.cfg).build(odb)
    assert ob["partial_games"] == {"forfeits": 1, "full_game_rounds": 24.0}, ob["partial_games"]
    kills = next(mk for mk in ob["player_props"] if mk["market_id"] == "ou:kills:puuid-1")
    assert kills["mean"] == 20.0 and kills["line"] > 19, kills  # 10 kills in half a game counts as 20, not 10
    assert "team:rounds" not in [mk["market_id"] for mk in ob["team"]]  # total rounds is off the board (old bets still settle)


@section("forecasts")
def forecasts(shared):
    # Thirteen games: P1 plays Jett on Ascent (even games, 20 kills) and Sova on Bind (odd games, 10 kills); game 9 is
    # a surrender. P1 has 15 deaths a game, except 25 in game 10 (Ascent) and 5 in game 11 (Bind). Game 12 is a 30-round
    # overtime game on Ascent with the usual 0.9 kills per round, so 27 kills.
    fdb, engine = DB(os.path.join(shared.tmp, "forecasts.db")), OddsEngine(shared.cfg)
    for i, pu in enumerate(PUUIDS.values()):
        fdb.upsert_member(pu, f"P{i + 1}", "TAG", "na", "", None, i)
    for k in range(13):
        if k == 11:  # what the odds board said just before the last game, for P1 on Bind as Sova
            board = engine.build(fdb, {"map": "Bind", "agents": {"puuid-1": "Sova"}})
            board_kills = next(mk for mk in board["player_props"] if mk["market_id"] == "ou:kills:puuid-1")
        bind = k % 2 == 1
        rw, rl = {9: (8, 4), 12: (16, 14)}.get(k, (13, 9))
        p1 = {"agent": "Sova" if bind else "Jett", "kills": {12: 27}.get(k, 10 if bind else 20), "deaths": {10: 25, 11: 5}.get(k, 15)}
        fdb.insert_match({"match_id": f"f{k}", "map": "Bind" if bind else "Ascent", "mode": "competitive",
                          "started_ts": 1000 + k, "rounds_won": rw, "rounds_lost": rl, "result": "win"},
                         [{"puuid": pu, "agent": "Omen", "kills": 15, "deaths": 15, "assists": 4, "score": 4800,
                           "damage_dealt": 3000, "headshots": 5, "bodyshots": 20, "legshots": 1,
                           **(p1 if pu == "puuid-1" else {})} for pu in PUUIDS.values()])
    fk = build_forecasts(fdb, engine, "kills")
    p = fk["player"]
    assert p["puuid"] == "puuid-1" and [x["nickname"] for x in fk["players"]] == ["P1", "P2", "P3", "P4", "P5"]
    # From the 6th game on (5 earlier ones), surrender excluded, oldest first.
    assert [g["match_id"] for g in p["games"]] == ["f5", "f6", "f7", "f8", "f10", "f11", "f12"], p["games"]
    game = {g["match_id"]: g for g in p["games"]}
    f11, f10 = game["f11"], game["f10"]
    # The forecast is what the odds board showed before that game: its average, and the typical game at its line.
    assert f11["expected"] == board_kills["mean"] and abs(f11["typical"] - board_kills["line"]) <= 1, (f11, board_kills["line"])
    assert f11["range"][0] < 10 < f11["range"][1] and f11["expected"] < 15 < f10["expected"]  # the map and agent pull it
    assert f11["role"] == "Initiator" and f10["role"] == "Duelist"
    # The next game's forecast is what the odds board shows now, for the same map and agent.
    assert p["next"] and p["next"]["map"] is None and p["next"]["range"][0] < p["next"]["typical"] < p["next"]["range"][1], p["next"]
    now = next(mk for mk in engine.build(fdb, {"map": "Bind", "agents": {"puuid-1": "Sova"}})["player_props"] if mk["market_id"] == "ou:kills:puuid-1")
    nxt = build_forecasts(fdb, engine, "kills", "puuid-1", "Bind", "Sova")["player"]["next"]
    assert nxt["expected"] == now["mean"] and (nxt["map"], nxt["agent"], nxt["games"]) == ("Bind", "Sova", 13), (nxt, now["mean"])
    # The overtime game's 27 kills beat the per-game forecast only because it went long; per round it's a normal game.
    assert game["f12"]["actual"] > game["f12"]["range"][1], game["f12"]
    kpr = {g["match_id"]: g for g in build_forecasts(fdb, engine, "kpr")["player"]["games"]}["f12"]
    assert kpr["actual"] == 0.9 and kpr["range"][0] <= 0.9 <= kpr["range"][1], kpr
    # A skewed stat gets a lopsided range: eight 10-kill games and two 30-kill games put the typical game below
    # the average and stretch the top of the range further than the bottom.
    low, typical, high, expected = OddsEngine.stat_range([(1.0, {"kills": v}) for v in [10] * 8 + [30] * 2], "kills")
    assert typical < expected == 14.0 and high - typical > typical - low, (low, typical, high, expected)
    cells = {(c["map"], c["role"]): c for c in p["cells"]}
    assert cells[("Ascent", "Duelist")]["games"] == 4 and cells[("Bind", "Initiator")]["games"] == 3
    assert cells[("Ascent", None)]["games"] == 4 and cells[(None, "Duelist")]["games"] == 4
    assert all(c["above"] + c["inside"] + c["below"] == c["games"] for c in p["cells"])
    assert cells[("Bind", "Initiator")]["agents"] == [{"agent": "Sova", "games": 3, "actual": 10.0,
                                                        "expected": cells[("Bind", "Initiator")]["expected"]}]
    assert all(c["low"] <= c["typical"] <= c["high"] for c in p["cells"])
    assert [x["forecast_games"] for x in fk["players"]] == [7] * 5  # games 5-12 minus the surrender, for everyone
    # Deaths: fewer is better, so the Bind game with 5 deaths is where P1 beats the forecast, per game and per round.
    for key in ("deaths", "dpr"):
        fd = build_forecasts(fdb, engine, key)
        assert fd["player"]["best"]["map"] == "Bind" and fd["player"]["best"]["diff"] < 0, fd["player"]["best"]
        assert fd["player"]["worst"]["map"] == "Ascent" and fd["player"]["worst"]["diff"] > 0, fd["player"]["worst"]
    assert [(s["key"], s["group"]) for s in fd["stats"]][:6] == [("kills", "game"), ("deaths", "game"), ("assists", "game"),
                                                                 ("kpr", "round"), ("dpr", "round"), ("apr", "round")]
    assert build_forecasts(fdb, engine, "acs", "puuid-3")["player"]["puuid"] == "puuid-3"
    assert build_forecasts(fdb, engine, "acs", "nobody")["player"]["puuid"] == "puuid-1"  # unknown player: the first
    try:
        build_forecasts(fdb, engine, "kd")
        raise AssertionError("an unknown stat should be refused")
    except ValueError:
        pass
    few = build_forecasts(shared.db, shared.engine)  # the main test database has 4 games: too few to forecast
    assert few["player"]["overall"] is None and few["player"]["cells"] == [] and few["player"]["games"] == []


@section("recap")
def recap(shared):
    # Twelve ordinary games, one a day, the last three lost; then a staged comeback win on Ascent. P1 normally gets
    # 15-20 kills, P2 always plays Sova, P3 is always Gold 3.
    rdb, engine = DB(os.path.join(shared.tmp, "recap.db")), OddsEngine(shared.cfg)
    for i, pu in enumerate(PUUIDS.values()):
        rdb.upsert_member(pu, f"P{i + 1}", "TAG", "na", "", None, i)

    def game(mid, k, won, lines):
        rdb.insert_match({"match_id": mid, "map": "Ascent", "mode": "competitive", "mode_label": "Competitive",
                          "started_ts": 1000 + k * 86400, "rounds_won": 13 if won else 9, "rounds_lost": 9 if won else 13,
                          "result": "win" if won else "loss"},
                         [{"puuid": pu, "agent": "Jett", "kills": 16, "deaths": 15, "assists": 4, "score": 4800,
                           "damage_dealt": 3000, "headshots": 5, "bodyshots": 20, "legshots": 1, "tier": 10, "tier_name": "Gold 3",
                           **lines.get(pu, {})} for pu in PUUIDS.values()])
    for k in range(12):
        game(f"g{k}", k, k < 9, {"puuid-1": {"kills": 15 + k % 6}, "puuid-2": {"agent": "Sova"}})
    game("final", 12, True, {"puuid-1": {"kills": 30}, "puuid-2": {"agent": "Clove"},
                             "puuid-3": {"tier": 11, "tier_name": "Platinum 1"}})
    rdb.execute("UPDATE matches SET rounds_won=13, rounds_lost=11 WHERE match_id='final'")
    # The final's rounds: 0-8 down, then 13 of the last 16. Round 9: P1 aces. Round 10: P2 clutches a 1v3.
    # Round 11: a knife kill by P4. Round 12: P5 kills teammate P3.
    us, them = list(PUUIDS.values()), [f"e{i}" for i in range(1, 6)]
    wins = [False] * 8 + [True, True, True, False] * 3 + [True] * 4
    kill = lambda r, t, a, b, w="Vandal": {"r": r, "t": t, "killer": a, "victim": b, "weapon": w}  # noqa: E731
    kills = [kill(8, 1000 + i, "puuid-1", e) for i, e in enumerate(them)]
    kills += [kill(9, 1000, "puuid-2", "e1"), kill(9, 2000, "puuid-2", "e2")]
    kills += [kill(9, 3000 + i, "e3", u) for i, u in enumerate(["puuid-1", "puuid-3", "puuid-4", "puuid-5"])]
    kills += [kill(9, 9000, "puuid-2", "e3"), kill(10, 1000, "puuid-4", "e1", "Melee"), kill(11, 1000, "puuid-5", "puuid-3")]
    rdb.save_timeline("final", {"our_team": "Blue", "team_of": {**{u: "Blue" for u in us}, **{e: "Red" for e in them}},
                                "rounds": [{"winner": "Blue" if w else "Red", "site": None, "planter_team": None, "defused": False} for w in wins],
                                "kills": kills})
    # Bets settled on the final: an underdog match-result win, a long shot, a bet on yourself, a parlay.
    for bettor, mtype, market, sel, odds, ctx in [
            ("P4", "team_win", "team:win", "win", 3.0, {"fair_prob": 0.3}),
            ("P5", "top", "top:kills", "puuid-1", 6.0, {"stat": "kills"}),
            ("P1", "ou", "ou:kills:puuid-1", "over", 1.9, {"stat": "kills", "puuid": "puuid-1"}),
            ("P2", "parlay", "parlay", "parlay", 3.6, {"legs": [{}, {}]})]:
        bid = rdb.insert_bet({"bettor": bettor, "market_id": market, "market_type": mtype, "description": market, "selection": sel,
                              "selection_label": sel, "line": None, "odds_decimal": odds, "stake": 10.0, "placed_ts": 0,
                              "context": json.dumps(ctx), "status": "won"})
        rdb.execute("UPDATE bets SET settled_match_id='final', payout=? WHERE id=?", (10.0 * odds, bid))

    r = build_recap(rdb, engine)  # the latest game by default
    assert r["match"]["match_id"] == "final" and r["match"]["number"] == 13 and r["match"]["older"] == "g11" and r["match"]["newer"] is None
    hl = {(h["kind"], h["puuid"]): h for h in r["highlights"]}  # the first (highest-scoring) of each kind per player
    titles = {h["title"] for h in r["highlights"]}
    assert "Most kills in a squad game: 30" in titles, titles
    assert "Lowest ACS in a squad game: 200" in titles  # 4800 score over 24 rounds; averages say highest / lowest
    assert hl[("ace", "puuid-1")] and hl[("clutch", "puuid-2")]["title"] == "Won a 1v3 clutch"
    assert {h["title"] for h in r["highlights"] if h["kind"] == "first"} == {"First squad game on Clove", "First time playing Controller"}
    assert hl[("rank", "puuid-3")]["title"] == "New peak rank: Platinum 1"
    assert hl[("knife", "puuid-4")]["detail"] == "Round 11" and hl[("team_kill", "puuid-5")]["detail"] == "Round 12"
    assert hl[("comeback", None)]["title"] == "Came back from 0–8 down"
    assert hl[("streak", None)]["title"] == "Snapped a 3-game losing streak"
    assert hl[("upset", None)]["detail"] == "The odds gave the squad 30%"
    assert hl[("longshot", "puuid-5")]["title"] == "P5 hit a +500 long shot" and hl[("parlay", "puuid-2")]
    assert not any(h["kind"] == "self_bet" for h in r["highlights"])  # betting on yourself isn't a highlight any more
    scores = [h["score"] for h in r["highlights"]]
    assert scores == sorted(scores, reverse=True) and r["highlights"][0]["kind"] == "ace", r["highlights"][:3]
    # Round by round, and the scoreboard against each player's usual game and forecast.
    assert len(r["rounds"]) == 24 and r["rounds"][7]["score"] == [0, 8] and r["rounds"][8]["multi"] == {"puuid-1": 5}
    assert r["rounds"][9]["clutch"] == {"puuid": "puuid-2", "vs": 3, "won": True}
    board = {p["puuid"]: p for p in r["players"]}
    assert board["puuid-1"]["kills"] == 30 and board["puuid-1"]["usual"]["kills"] == 17.5 and board["puuid-1"]["forecast"]["kills"]
    assert board["puuid-3"]["rank_change"] == 1 and board["puuid-2"]["role"] == "Controller"
    assert board["puuid-1"]["rounds"]["k5"] == 1 and board["puuid-2"]["rounds"]["clutches"] == [{"round": 10, "vs": 3, "won": True}]
    assert r["betting"]["bets"] == 4 and r["betting"]["house"] == -(20 + 50 + 9 + 26)
    # An older game only knows what came before it: game 6 has 5 earlier games, too few for records.
    old = build_recap(rdb, engine, "g5")
    assert old["match"]["number"] == 6 and not [h for h in old["highlights"] if h["kind"] in ("record", "near_record")]
    assert build_recap(shared.db, shared.engine)["match"]["match_id"] and build_recap(DB(os.path.join(shared.tmp, "empty.db")), engine) is None


@section("custom lines")
def custom_lines(shared):
    # Ten games in which P1 gets 14-26 kills; custom lines on them are priced like the board's own line.
    cdb, engine = DB(os.path.join(shared.tmp, "custom.db")), OddsEngine(shared.cfg)
    cbets = BetManager(shared.cfg, cdb, engine)
    for i, pu in enumerate(PUUIDS.values()):
        cdb.upsert_member(pu, f"P{i + 1}", "TAG", "na", "", None, i)

    def play(k, p1_kills, ts=None):
        cdb.insert_match({"match_id": f"c{k}", "map": "Ascent", "mode": "competitive", "started_ts": ts or 1000 + k,
                          "rounds_won": 13, "rounds_lost": 9, "result": "win"},
                         [{"puuid": pu, "agent": "Jett", "kills": p1_kills if pu == "puuid-1" else 15, "deaths": 14,
                           "assists": 4, "score": 4800, "damage_dealt": 3000, "headshots": 5, "bodyshots": 20, "legshots": 1}
                          for pu in PUUIDS.values()])
    for k, kills in enumerate([14, 22, 17, 26, 19, 15, 24, 18, 21, 16]):
        play(k, kills)
    board = engine.build(cdb, {}, alts=[("kills", "puuid-1", 24.5), ("kills", "puuid-1", 60.5), ("kills", "puuid-1", 20.0),
                                        ("kills", "puuid-1", float("nan")), ("kills", "nobody", 20.5)])
    ok, far, whole, nan = board["custom"]  # an unknown player is simply left out
    assert ok["available"] and ok["market_id"] == "alt:kills:puuid-1:24.5" and ok["custom"] and ok["type"] == "ou"
    assert abs(sum(s["fair_prob"] for s in ok["selections"]) - 1) < 1e-3 and ok["selections"][0]["fair_prob"] < 0.5
    assert not far["available"] and "too far" in far["reason"] and not whole["available"] and "whole numbers" in whole["reason"]
    assert not nan["available"]
    lo, hi = ok["limits"]["at_least"]
    assert lo <= ok["typical"] <= hi and 25 <= hi < 61, ok["limits"]  # "at least 25" is fine, "at least 61" isn't
    # A custom line at the board's own number costs exactly what the board charges.
    std = next(mk for mk in board["player_props"] if mk["market_id"] == "ou:kills:puuid-1")
    same = engine.build(cdb, {}, alts=[("kills", "puuid-1", std["line"])])["custom"][0]
    assert [s["decimal"] for s in same["selections"]] == [s["decimal"] for s in std["selections"]], (same, std)
    # Betting on them: singles and parlay legs, one line per player and stat in a parlay.
    cbets.register("Cus", "secret1")
    single = cbets.place("Cus", "alt:kills:puuid-1:21.5", "over", 10, {})
    meta = json.loads(single["context"])
    assert single["market_type"] == "ou" and single["line"] == 21.5 and meta["custom"] and meta["puuid"] == "puuid-1"
    assert single["description"] == "P1 Kills Over 21.5" and 0 < meta["fair_prob"] < 1
    # Over 13.5 is a near-lock (about 92%): not offered, while its long-shot under still is.
    lock = engine.build(cdb, {}, alts=[("kills", "puuid-1", 13.5)])["custom"][0]
    assert [(x["key"], x["available"]) for x in lock["selections"]] == [("over", False), ("under", True)], lock["selections"]
    assert lock["limits"]["at_least"][0] > 14  # "at least 14" is that same near-lock
    assert cbets.place("Cus", "alt:kills:puuid-1:13.5", "under", 5, {})["line"] == 13.5
    for bad, why in (("alt:kills:puuid-1:60.5", "too far"), ("alt:kills:puuid-1:abc", "no longer available"),
                     ("alt:kills:puuid-1:13.5", "nearly certain")):
        try:
            cbets.place("Cus", bad, "over", 10, {})
            raise AssertionError(f"{bad} should be refused")
        except BetError as e:
            assert why in str(e), e
    try:
        cbets.place_parlay("Cus", [{"market_id": "ou:kills:puuid-1", "selection": "over"},
                                   {"market_id": "alt:kills:puuid-1:21.5", "selection": "over"}], 10, {})
        raise AssertionError("a parlay can't hold two lines on the same player and stat")
    except BetError as e:
        assert "one line per player and stat" in str(e), e
    parlay = cbets.place_parlay("Cus", [{"market_id": "alt:kills:puuid-1:21.5", "selection": "over"},
                                        {"market_id": "alt:deaths:puuid-2:15.5", "selection": "under"}], 10, {})
    assert [leg["line"] for leg in json.loads(parlay["context"])["legs"]] == [21.5, 15.5]
    # Exact numbers: the smoothed distribution's share at exactly N, on the counting stats only.
    exact = engine.build(cdb, {}, alts=[("kills", "puuid-1", n, "exact") for n in range(60)])["custom"]
    assert abs(sum(x["fair_prob"] for mk in exact for x in mk["selections"]) - 1) < 0.01  # the chances cover everything
    by_n = {mk["line"]: mk for mk in exact}
    assert by_n[19]["available"] and by_n[19]["type"] == "exact" and not by_n[45]["available"] and "too unlikely" in by_n[45]["reason"]
    lo, hi = by_n[19]["limits"]["exactly"]
    assert lo < 19 < hi and all(by_n[n]["available"] == (lo <= n <= hi) for n in range(60)), (lo, hi)
    odd = engine.build(cdb, {}, alts=[("hs_pct", "puuid-1", 20, "exact"), ("kills", "puuid-1", 19.5, "exact")])["custom"]
    assert "only for kills, deaths and assists" in odd[0]["reason"] and "whole numbers" in odd[1]["reason"]
    hit = cbets.place("Cus", "exact:kills:puuid-1:25", "exact", 5, {})
    miss = cbets.place("Cus", "exact:kills:puuid-1:21", "exact", 5, {})
    assert hit["market_type"] == "exact" and hit["description"] == "P1 Kills Exactly 25" and json.loads(hit["context"])["custom"]
    assert abs(fair_chance(hit["odds_decimal"], "exact", 0.05) - by_n[25]["selections"][0]["fair_prob"]) < 0.01  # double edge
    try:
        cbets.place_parlay("Cus", [{"market_id": "exact:kills:puuid-1:20", "selection": "exact"},
                                   {"market_id": "alt:kills:puuid-1:21.5", "selection": "over"}], 5, {})
        raise AssertionError("an exact number and a line on the same player and stat can't share a parlay")
    except BetError as e:
        assert "one line per player and stat" in str(e), e
    # A surrender settles an exact number only if it was already passed (lost); otherwise it's refunded.
    ev = lambda n, kills: cbets._evaluate({"market_type": "exact", "selection": "exact", "line": n,  # noqa: E731
                                           "context": json.dumps({"stat": "kills", "puuid": "p"})},
                                          {"mode": "competitive", "rounds_won": 9, "rounds_lost": 4, "result": "win"}, {"p": {"kills": kills}})[0]
    assert ev(10, 12) == "lost" and ev(15, 12) == "void"
    # They settle like any over / under: P1 gets 25 kills, P2 dies 14 times.
    play(10, 25, time.time() + 5)  # starts after the bets were placed
    settled = {b["id"]: b for b in cbets.settle_for_match(cdb.match("c10"), cdb.match_players("c10"))}
    assert settled[single["id"]]["status"] == "won" and settled[parlay["id"]]["status"] == "won", settled
    assert settled[hit["id"]]["status"] == "won" and settled[miss["id"]]["status"] == "lost"  # exactly 25, not 21


@section("team moments")
def team_moments(shared):
    # Team markets read from the round timeline: pistol, ahead at half-time, an ace, a comeback, flawless rounds.
    team_of = {**{p: "Blue" for p in PUUIDS.values()}, **{f"e{i}": "Red" for i in range(5)}}

    def tl(won, kills=()):  # won: one bool per round (did the squad win it); kills: (round, killer, victim) in order
        return {"our_team": "Blue", "team_of": team_of,
                "rounds": [{"winner": "Blue" if w else "Red", "site": None, "planter_team": None, "defused": False} for w in won],
                "kills": [{"r": r, "t": i, "killer": k, "victim": v, "weapon": "Vandal"} for i, (r, k, v) in enumerate(kills)]}
    # A 13-9 comeback from 0-5: rounds 1-5 lost, then 13 of the last 17. Only one squad death (round 1), so every
    # round won is flawless; P1 aces round 6. 6 of the first 12 is level at half-time, not ahead.
    comeback_won = [False] * 5 + [True] * 4 + [False] + [True] * 3 + [False] + [True] * 2 + [False] + [True] * 2 + [False] + [True] * 2
    ace = [(5, "puuid-1", f"e{i}") for i in range(5)]
    facts = game_facts({"result": "win", "mode": "competitive"}, tl(comeback_won, [(0, "e0", "puuid-2")] + ace))
    assert facts == {"pistol": False, "half": False, "ace": True, "comeback": True, "flawless": 13}, facts
    assert game_facts({"result": "win"}, None) == {k: None for k in facts}  # no timeline: nothing is decided
    assert game_facts({"result": "win", "mode": "swiftplay"}, tl([True] * 5))["half"] is None  # no round 12
    assert game_facts({"result": "loss"}, tl([True] * 12 + [False] * 13))["half"] is True  # 12-0 up at the half

    # Priced from the games that have a timeline, once MOMENT_MIN_GAMES of them decide a market.
    mdb, engine = DB(os.path.join(shared.tmp, "moments.db")), OddsEngine(shared.cfg)
    mbets = BetManager(shared.cfg, mdb, engine)
    for i, pu in enumerate(PUUIDS.values()):
        mdb.upsert_member(pu, f"P{i + 1}", "TAG", "na", "", None, i)
    line = lambda k: [{"puuid": pu, "agent": "Omen", "kills": 15, "deaths": 15, "assists": 4, "score": 4800, "damage_dealt": 3000,  # noqa: E731
                       "headshots": 5, "bodyshots": 20, "legshots": 1} for pu in PUUIDS.values()]
    regular = [True] * 7 + [False] * 5 + [True] * 6 + [False] * 4  # pistol won, 7-5 at the half, 13-9, no deaths recorded
    for k in range(8):
        won = regular if k < 6 else [False] + regular[1:]  # the two oldest games lost the pistol
        mdb.insert_match({"match_id": f"g{k}", "map": "Bind", "mode": "competitive", "started_ts": 1000 + (8 - k),
                          "rounds_won": 13, "rounds_lost": 9, "result": "win"}, line(k))
        mdb.save_timeline(f"g{k}", tl(won, ace if k == 0 else ()))
    board = engine.build(mdb)
    team = {mk["market_id"]: mk for mk in board["team"]}
    assert [m for m in team if m.startswith("team:") and team[m]["type"] == "team_moment"] == \
        ["team:pistol", "team:half", "team:ace", "team:comeback", "team:flawless"], list(team)
    assert team["team:pistol"]["basis"] == {"games": 8, "hits": 6} and 0.5 < team["team:pistol"]["selections"][0]["fair_prob"] < 0.9
    assert team["team:ace"]["basis"]["hits"] == 1 and team["team:comeback"]["selections"][0]["fair_prob"] < 0.2
    assert team["team:flawless"]["line"] % 1 == 0.5 and team["team:flawless"]["selections"][0]["label"].startswith("Over ")
    few = engine.moment_markets(mdb.matches(), {t["match_id"]: t["data"] for t in mdb.timelines()[:4]})
    assert few == [], few  # 4 games with a timeline: not enough to offer any

    # Settled from the new game's timeline; a game without one refunds them; a parlay replays them like any leg.
    mbets.register("Moe", "secret1")
    pistol = mbets.place("Moe", "team:pistol", "yes", 10, {})
    ace_no = mbets.place("Moe", "team:ace", "no", 10, {})
    flaw = mbets.place("Moe", "team:flawless", "over", 10, {})
    assert pistol["description"] == "Pistol round: Yes" and json.loads(pistol["context"])["fact"] == "pistol", pistol
    legs, _, quote = mbets.quote_parlay([{"market_id": "team:pistol", "selection": "yes"}, {"market_id": "team:win", "selection": "win"}], {})
    assert set(legs[0]["hist"]) <= {"0", "1"} and len(legs[0]["hist"]) == 8, legs[0]["hist"]  # replayed on every game
    mdb.insert_match({"match_id": "g-new", "map": "Bind", "mode": "competitive", "started_ts": time.time() + 5,
                      "rounds_won": 13, "rounds_lost": 9, "result": "win"}, line(9))
    mdb.save_timeline("g-new", tl(comeback_won, [(0, "e0", "puuid-2")] + ace))
    settled = {b["id"]: b for b in mbets.settle_for_match(mdb.match("g-new"), mdb.match_players("g-new"))}
    assert [settled[b["id"]]["status"] for b in (pistol, ace_no, flaw)] == ["lost", "lost", "won"], settled
    assert settled[flaw["id"]]["actual_value"] == 13
    again = mbets.place("Moe", "team:pistol", "yes", 10, {})
    mdb.insert_match({"match_id": "g-bare", "map": "Bind", "mode": "competitive", "started_ts": time.time() + 10,
                      "rounds_won": 13, "rounds_lost": 5, "result": "win"}, line(10))  # its full record never came
    bare = mbets.settle_for_match(mdb.match("g-bare"), mdb.match_players("g-bare"))[0]
    assert bare["id"] == again["id"] and bare["status"] == "void" and "isn't available" in bare["note"], bare

    # A surrender: the pistol and an ace that happened are decided; half-time before round 12, or no ace yet, is void.
    ev = lambda fact, sel, won, kills=(), ln=None: mbets._evaluate(  # noqa: E731
        {"market_type": "team_moment", "selection": sel, "line": ln, "context": json.dumps({"fact": fact})},
        {"mode": "competitive", "rounds_won": sum(won), "rounds_lost": len(won) - sum(won), "result": "win",
         "timeline": tl(won, kills)}, {})
    ff = [True, False, True, True, True, True, False, True]  # surrendered by them 6-2
    assert ev("pistol", "yes", ff)[0] == "won" and "surrender" in ev("pistol", "yes", ff)[2]
    assert ev("half", "yes", ff)[0] == "void" and ev("ace", "no", ff)[0] == "void" and ev("ace", "yes", ff, ace)[0] == "won"
    assert ev("flawless", "over", ff, ln=4.5)[0] == "won" and ev("flawless", "under", ff, ln=7.5)[0] == "void"


@section("score markets")
def score_markets(shared):
    # Margin and exact score come from one final-score model that agrees with the match-result and overtime markets.
    board = shared.engine.build(shared.db)
    team = {mk["market_id"]: mk for mk in board["team"]}
    fair = lambda mid: {x["key"]: x["fair_prob"] for x in team[mid]["selections"]}  # noqa: E731
    score, margin = fair("team:score"), fair("team:margin")
    # Every result is offered, in the page's order: a heavy loss to a heavy win, and 0-13 .. overtime .. 13-0.
    assert list(margin) == ["l6+", "l3-5", "l1-2", "w1-2", "w3-5", "w6+"]
    assert list(score) == [f"{x}-13" for x in range(12)] + ["ot-loss", "ot-win"] + [f"13-{x}" for x in range(11, -1, -1)]
    p_win, p_ot = fair("team:win")["win"], fair("team:ot")["yes"]
    assert abs(sum(v for k, v in margin.items() if k.startswith("w")) - p_win) < 2e-3, (margin, p_win)
    assert abs(sum(v for k, v in score.items() if k.startswith("13-") or k == "ot-win") - p_win) < 2e-3
    assert abs(sum(margin.values()) - 1) < 2e-3 and abs(sum(score.values()) - 1) < 2e-3
    assert margin["w1-2"] > score["13-11"] and team["team:score"]["selections"][-1]["label"] == "13–0"
    assert team["team:score"]["selections"][12]["label"] == "Overtime loss"
    assert "team:rw" not in team and "team:rl" not in team  # no longer offered; old bets on them still settle (below)
    # The model itself: matches the match-result chance, and the overtime chance unless that's below what it can
    # produce (about 6%, e.g. a history with no overtime yet), where it sits at its widest spread.
    for pw, pot in ((0.45, 0.10), (0.6, 0.03)):
        dist, mu, sd = score_model(pw, pot)
        won = sum(p for k, p in dist.items() if k == "ot-win" or (isinstance(k, tuple) and k[0] == 13))
        assert abs(won - pw) < 1e-3 and abs(sum(dist.values()) - 1) < 1e-9
        assert abs(dist["ot-win"] + dist["ot-loss"] - pot) < 0.03 or sd == SCORE_SD_MAX, (pw, pot, sd)
    # They settle on the final score; a surrender only settles rounds won / lost already past the line.
    ev = lambda mtype, sel, match, line=None: shared.bets._evaluate(  # noqa: E731
        {"market_type": mtype, "selection": sel, "line": line, "context": "{}"}, {"mode": "competitive", **match}, {})[0]
    w13_7 = {"rounds_won": 13, "rounds_lost": 7, "result": "win"}
    assert ev("team_score", "13-7", w13_7) == "won" and ev("team_score", "13-6", w13_7) == "lost"
    assert ev("team_margin", "w6+", w13_7) == "won" and ev("team_margin", "w3-5", w13_7) == "lost"
    assert ev("team_rw", "over", w13_7, 10.5) == "won" and ev("team_rl", "under", w13_7, 10.5) == "won"
    ot = {"rounds_won": 15, "rounds_lost": 13, "result": "win"}
    assert ev("team_score", "13-11", ot) == "lost" and ev("team_margin", "w1-2", ot) == "won" and ev("team_rl", "over", ot, 11.5) == "won"
    assert ev("team_score", "ot-win", ot) == "won" and ev("team_score", "ot-loss", ot) == "lost"
    l7_13 = {"rounds_won": 7, "rounds_lost": 13, "result": "loss"}  # losses have their own picks now
    assert ev("team_score", "13-7", l7_13) == "lost" and ev("team_margin", "w6+", l7_13) == "lost"
    assert ev("team_score", "7-13", l7_13) == "won" and ev("team_margin", "l6+", l7_13) == "won" and ev("team_margin", "l3-5", l7_13) == "lost"
    assert ev("team_score", "5-3", {"rounds_won": 5, "rounds_lost": 3, "result": "win", "mode": "swiftplay"}) == "void"
    ff = {"rounds_won": 9, "rounds_lost": 4, "result": "win"}  # surrendered 9-4
    assert ev("team_rw", "over", ff, 8.5) == "won" and ev("team_rw", "over", ff, 10.5) == "void"
    assert ev("team_rl", "over", ff, 3.5) == "won" and ev("team_score", "13-4", ff) == "void" and ev("team_margin", "w3-5", ff) == "void"
    # Placing one: described by market and pick, priced with the multi-way edge.
    shared.bets.register("Scorer", "secret1")
    bet = shared.bets.place("Scorer", "team:score", "13-11", 10, {})
    assert bet["market_type"] == "team_score" and bet["description"] == "Exact score: 13–11", bet
    assert abs(fair_chance(bet["odds_decimal"], "team_score", 0.05) - score["13-11"]) < 0.01
    shared.bets.cancel(bet["id"], by="Scorer")


@section("streaks")
def streaks(shared):
    # A pick that would have won each of the last 3+ games, settled at today's line, carries a "streak" count.
    from fivestack.bets import COLD_CHANCE, STREAK_LOOKBACK, STREAK_MIN
    board = shared.bets.mark_streaks(shared.engine.build(shared.db))
    assert board["streak_lookback"] == STREAK_LOOKBACK
    wins = 0
    for m in shared.db.matches(limit=STREAK_LOOKBACK):  # newest first; a draw neither counts nor breaks the run
        if m["result"] == "draw":
            continue
        if m["result"] != "win":
            break
        wins += 1
    sels = {mk["market_id"]: {s["key"]: s for s in mk["selections"]} for g in ("team", "player_props", "top_markets") for mk in board[g]}
    assert sels["team:win"]["win"].get("streak", 0) == (wins if wins >= STREAK_MIN else 0), (sels["team:win"], wins)
    assert "streak" not in sels["team:win"]["loss"] or wins == 0
    hot = [s for picks in sels.values() for s in picks.values() if "streak" in s]
    assert hot and all(STREAK_MIN <= s["streak"] <= STREAK_LOOKBACK for s in hot), hot
    for mid, picks in sels.items():  # an over and its under can't both have won the same games
        if mid.startswith("ou:"):
            assert not ("streak" in picks["over"] and "streak" in picks["under"]), picks
    # Cold: a roughly 50/50 pick that missed the last 3+ games (long shots never are), only in markets with 3+ picks:
    # in a two-way market the other side of a cold pick already has the flame.
    cold = [s for picks in sels.values() if len(picks) > 2 for s in picks.values() if "cold" in s]
    assert all("streak" not in s and s["cold"] >= STREAK_MIN and COLD_CHANCE[0] <= s["fair_prob"] <= COLD_CHANCE[1] for s in cold), cold
    assert not any("cold" in s for picks in sels.values() if len(picks) == 2 for s in picks.values())
    # Priced at 50%, every multi-way pick that lost its last 3+ games is cold; two-way picks still never are.
    even = shared.engine.build(shared.db)
    for g in ("team", "player_props", "top_markets"):
        for mk in even[g]:
            for s in mk["selections"]:
                s["fair_prob"] = 0.5
    even = shared.bets.mark_streaks(even)
    even = {mk["market_id"]: {s["key"]: s for s in mk["selections"]} for g in ("team", "top_markets") for mk in even[g]}
    assert "cold" not in even["team:win"]["loss"], even["team:win"]  # the other side of the win streak: no frost
    assert all(len(picks) > 2 for picks in even.values() if any("cold" in s for s in picks.values()))
    assert any("cold" in s for picks in even.values() for s in picks.values()), "no multi-way pick went cold at 50%"
    assert shared.bets.mark_streaks({"ready": False}) == {"ready": False}


@section("grace and cancel windows")
def grace_and_cancel(shared):
    cfg, engine = shared.cfg, shared.engine
    # Grace period: a bet placed while loading in (1 minute after the game's start time) is for that game; one placed
    # 10 minutes in waits for the next game. With the grace set to 0 the old rule applies.
    gdb = DB(os.path.join(shared.tmp, "grace.db"))
    gdb.execute("INSERT INTO bettors(name, balance, created_at) VALUES('G', 1000, 0)")
    game = {"match_id": "g1", "mode": "competitive", "started_ts": 10_000.0, "rounds_won": 13, "rounds_lost": 5, "result": "win"}
    for offset in (-300, 60, 600):
        gdb.insert_bet({"bettor": "G", "market_id": "team:win", "market_type": "team_win", "description": "5-stack wins",
                        "selection": "win", "selection_label": "5-stack wins", "line": None, "odds_decimal": 2.0, "stake": 10.0,
                        "placed_ts": game["started_ts"] + offset, "context": "{}", "status": "pending"})
    settled = BetManager(cfg, gdb, engine).settle_for_match(game, [])
    assert sorted(b["placed_ts"] - game["started_ts"] for b in settled) == [-300, 60], settled
    assert [b["placed_ts"] - game["started_ts"] for b in gdb.pending_bets()] == [600]  # carries over to the next game
    assert BetManager({**cfg, "bet_grace_minutes": 0}, gdb, engine).grace_s == 0
    # Cancelling: a bettor has 1 minute after placing a bet; the admin can cancel any open bet.
    gm = BetManager(cfg, gdb, engine)
    late = gdb.pending_bets()[0]  # placed long ago
    try:
        gm.cancel(late["id"], by="G")
        raise AssertionError("a bet older than a minute can't be cancelled by its bettor")
    except BetError as e:
        assert "within 1 minute" in str(e), e
    fresh = gdb.insert_bet({**{k: late[k] for k in ("bettor", "market_id", "market_type", "description", "selection",
                                                     "selection_label", "line", "odds_decimal", "stake", "context")},
                            "placed_ts": time.time() - 30, "status": "pending"})
    assert gm.cancel(fresh, by="g")["status"] == "cancelled"  # 30 seconds old: still allowed
    assert gm.cancel(late["id"], admin=True)["status"] == "cancelled"


@section("resync")
def resync(shared):
    client, tracker, db = shared.client, shared.tracker, shared.db
    # Second sync: nothing new, no duplicates, no re-verification of rejected ids.
    calls_before = client.calls
    res2 = shared.second_sync = tracker.sync()
    assert res2["ok"] and res2["new_matches"] == 0, res2
    # 5 stored-match calls, plus one full-record fetch each for m9 and m10 (inserted by hand above, so no round
    # timeline yet; the fake API 404s them, which is remembered). The next sync is back to just the 5.
    assert client.calls - calls_before == 7, client.calls - calls_before
    calls_before = client.calls
    assert tracker.sync()["ok"] and client.calls - calls_before == 5, client.calls - calls_before
    assert db.count_matches() == 4  # m1, m3, m9 and the reward test's m10
    assert db.count_member_games() == 17  # re-fetched lines are not duplicated

    # A database from before member_games existed gets one full history fetch, then goes back to normal.
    db.set_meta("history_backfilled", False)
    assert tracker.sync()["full"] is True and db.get_meta("history_backfilled") is True
    assert tracker.sync()["full"] is False and db.count_matches() == 4


@section("discord")
def discord(shared):
    from fivestack import discord as D

    # Only a real Discord webhook URL switches it on; anything else is ignored and nothing is ever sent.
    sent = []
    post = lambda url, payload: (sent.append((url, payload)) or (True, "HTTP 204"))  # noqa: E731
    hook = "https://discord.com/api/webhooks/123/abc"
    off, bad = D.Discord("", post=post, background=False), D.Discord("https://example.com/hook", post=post, background=False)
    assert not off.enabled and not off.ignored and not bad.enabled and bad.ignored
    assert off.send({"content": "x"}) is False and bad.send({"content": "x"}) is False and not sent
    on = D.Discord(f"  {hook}  ", post=post, background=False)
    assert on.enabled and on.send(D.test_message()) is True and sent == [(hook, D.test_message())]
    assert on.send(None) is False and len(sent) == 1
    # A post that fails (or raises) is reported as failed and never raises.
    assert D.Discord(hook, post=lambda u, p: (False, "HTTP 404"), background=False).send({"content": "x"}) is False
    assert D.Discord(hook, post=lambda u, p: 1 / 0, background=False).send({"content": "x"}) is False
    # In the background (the default) it reports the message as on its way.
    done = []
    bg = D.Discord(hook, post=lambda u, p: (done.append(p) or (True, "ok")))
    assert bg.send({"content": "bg"}) is True
    for _ in range(100):
        if done:
            break
        time.sleep(0.01)
    assert done == [{"content": "bg"}]

    # Only recent games are posted, a few per sync, oldest first: a first sync of a long history stays quiet.
    now = 2_000_000_000
    games = [{"match_id": f"g{i}", "started_ts": now - i * 3600} for i in range(12)]
    assert [m["match_id"] for m in D.fresh(games, now)] == ["g2", "g1", "g0"]
    assert D.fresh([{"match_id": "old", "started_ts": now - D.FRESH_S - 1}], now) == [] and D.fresh([], now) == []

    # The game post: result, the top of the scoreboard, the best highlight, the betting, and long shots.
    match = {"match_id": "m-d", "map": "Ascent", "result": "win", "rounds_won": 13, "rounds_lost": 11, "started_ts": 1_800_000_000}
    board = [{"nickname": "Matt", "kills": 18, "deaths": 15, "assists": 4, "acs": 221.4},
             {"nickname": "Jordan", "kills": 24, "deaths": 14, "assists": 6, "acs": 262.6}]
    bets = [{"bettor": "Matt", "status": "won", "stake": 50, "payout": 120, "odds_decimal": 2.4, "description": "Match result: Win"},
            {"bettor": "Matt", "status": "lost", "stake": 20, "payout": 0, "odds_decimal": 1.9, "description": "Matt Kills Over 20.5"},
            {"bettor": "Sam", "status": "lost", "stake": 60, "payout": 0, "odds_decimal": 2.1, "description": "Match result: Loss"},
            {"bettor": "Alex", "status": "won", "stake": 10, "payout": 75, "odds_decimal": 7.5, "description": "Final score: 13-11"},
            {"bettor": "Riley", "status": "void", "stake": 30, "payout": 30, "odds_decimal": 2.0, "description": "A push"}]
    msg = D.game_message(match, board, {"title": "Snapped a 3-game losing streak", "detail": "Squad"}, bets)
    embed = msg["embeds"][0]
    assert msg["username"] == "Onkey" and embed["title"] == "Win 13-11 on Ascent" and embed["color"] == D.WIN_COLOUR
    # Nothing a bettor types can ping the channel: every kind of post turns mentions off.
    pinger = [{"bettor": "@everyone", "status": "won", "stake": 1, "payout": 9, "odds_decimal": 9.0, "description": "@here"}]
    for post_ in (msg, D.game_message(match, board, None, pinger), D.jackpot_message("@everyone", 5), D.test_message()):
        assert post_["allowed_mentions"] == {"parse": []}, post_
    assert embed["description"] == "**Snapped a 3-game losing streak**\nSquad" and embed["timestamp"].startswith("2027-01-15")
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert fields["Top of the scoreboard"] == "**Jordan** · 24/14/6 · 263 ACS", fields
    assert fields["Betting"].split("\n") == ["🟢 **Alex** +65 (1 of 1 bet won)", "🟢 **Matt** +50 (1 of 2 bets won)",
                                            "🔴 **Sam** -60 (0 of 1 bet won)"], fields["Betting"]  # the void bet is left out
    assert fields["Long shot"] == '🎯 **Alex** hit "Final score: 13-11" at 7.50 for +65'
    loss = D.game_message({**match, "result": "loss", "rounds_won": 5, "rounds_lost": 13}, None, None, None)["embeds"][0]
    assert loss["title"] == "Loss 5-13 on Ascent" and loss["color"] == D.LOSS_COLOUR and loss["fields"] == [] and "description" not in loss
    # Discord's limits: a title is at most 256 characters and a field 1024, however much there is to say.
    many = [{"bettor": f"Bettor number {i} with a long name", "status": "won", "stake": 1, "payout": 3, "odds_decimal": 3.0,
             "description": "x" * 400} for i in range(40)]
    big = D.game_message({**match, "map": "M" * 400}, board, {"title": "T" * 3000, "detail": "d"}, many)["embeds"][0]
    assert len(big["title"]) <= 256 and len(big["description"]) <= 2000 and all(len(f["value"]) <= 1024 for f in big["fields"])
    assert "more" in big["fields"][1]["value"]
    jackpot = D.jackpot_message("Wes", 1284)["embeds"][0]
    assert jackpot["title"] == "JACKPOT!" and "**Wes**" in jackpot["description"] and "1,284 credits" in jackpot["description"]
    # A real game from this database goes through the recap into a post without tripping on anything.
    recap = build_recap(shared.db, shared.engine, None, {}, 150)
    real = D.game_message(recap["match"], recap["players"], (recap["highlights"] or [None])[0], shared.db.bets(limit=50))
    assert real["embeds"][0]["title"].split()[0] in ("Win", "Loss") and json.dumps(real)


@section("config docs")
def config_docs(shared):
    # Every setting in config.example.json is documented in the README's configuration table.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.example.json"), encoding="utf-8") as f:
        example_keys = set(json.load(f))
    with open(os.path.join(root, "README.md"), encoding="utf-8") as f:
        first_cells = re.findall(r"^\| ([^|]+) \|", f.read(), re.M)  # rows like "| `host` / `port` | ... |"
    documented = {key for cell in first_cells for key in re.findall(r"`([a-z_]+)`", cell)}
    undocumented = sorted(example_keys - documented)
    assert not undocumented, f"add these config.example.json keys to the README's configuration table: {undocumented}"


@section("roster")
def roster(shared):
    # The squad lives in the database (seeded from config.json by the first sync) and is edited at runtime.
    client, tracker, db = shared.client, shared.tracker, shared.db
    assert db.get_meta("roster_seeded") is True and db.count_members() == 5
    # Renames keep the account: a check updates the Riot ID and remembers the old one; once a day is enough.
    client.names["puuid-1"] = ("Pone", "NEW")
    renamed = tracker.refresh_names(force=True)
    assert [(r["from"], r["to"]) for r in renamed] == [("P1#TAG", "Pone#NEW")], renamed
    m1 = db.member("puuid-1")
    assert (m1["name"], m1["tag"], m1["previous_name"]) == ("Pone", "NEW", "P1#TAG"), m1
    assert tracker.refresh_names() == []
    # Entering a current member's new Riot ID updates them (and the nickname) instead of adding a second player.
    r = tracker.add_member("Pone#NEW", "one")
    assert r["added"] is False and db.count_members() == 5 and db.member("puuid-1")["nickname"] == "one", r
    # Two to five players, and only real Riot IDs.
    for bad, err in (("P6#TAG", "full"), ("nobody", "Riot ID"), ("Ghost#TAG", "Not found")):
        try:
            tracker.add_member(bad)
            raise AssertionError(f"{bad} should be refused")
        except (TrackerError, HenrikError) as e:
            assert err in str(e), (bad, str(e))
    games = db.count_matches()  # m1, m3, m9 and m10
    assert games == 4
    # Removing a player keeps every game the others all played (and finds the ones they played without them),
    # forgets rejections and forces a full re-scan.
    changes = tracker.remove_member("puuid-5")
    assert db.count_members() == 4 and changes == {"removed": 0, "added": 1, "was_active": True}, changes  # m2: P5 was on the other team
    assert db.get_meta("rejected_matches") == [] and db.get_meta("history_backfilled") is False
    assert db.count_matches() == games + 1
    # A newcomer was in none of the recorded games, so none count any more ...
    r = tracker.add_member("P6#TAG", "six")
    assert r["added"] and r["changes"] == {"removed": games + 1, "added": 0} and db.count_matches() == 0, r
    # ... and the next sync (full, because the squad changed) records the games all five did play together:
    # in the fake history P6 has m1, m2 and m3, and m2 only counts now because P5 (the one on the other team) is gone.
    res = tracker.sync()
    assert res["ok"] and res["full"] is True and res["members"] == 5, res
    assert {m["match_id"] for m in db.matches()} == {"m1", "m2", "m3"}, db.matches()
    # Back to the original five: what the stored lines prove comes back at once (m1), m2 is out again (P5 was on the
    # other team) and m3, which needs the full record for P5, returns on the next sync.
    tracker.remove_member("puuid-6")
    r = tracker.add_member("P5#TAG", "five")
    assert r["added"] and {m["match_id"] for m in db.matches()} == {"m1"}, r
    res = tracker.sync()
    assert res["ok"] and {m["match_id"] for m in db.matches()} == {"m1", "m3"}, db.matches()
    assert "m2" in db.get_meta("rejected_matches")
    # Down to a duo: their stored lines prove m2 and m5 too (both on the same team in each), and one is too few.
    for puuid in ("puuid-5", "puuid-4", "puuid-3"):
        tracker.remove_member(puuid)
    assert db.count_members() == 2 and {m["match_id"] for m in db.matches()} == {"m1", "m2", "m3", "m5"}, db.matches()
    try:
        tracker.remove_member("puuid-2")
        raise AssertionError("a squad of one should be refused")
    except TrackerError as e:
        assert "at least" in str(e)
    assert tracker.sync()["ok"] and db.count_matches() == 4
    # Squad games are never lost for good: the lines stay in member_games for whoever is on the squad next.
    assert db.count_member_games() >= 17

    # Self-service: a betting account carries its owner's Riot ID onto the squad, can change it, and can leave.
    bets = shared.bets
    bets.register("Newbie", "pw1234")
    r = tracker.set_riot_id("Newbie", "P3#TAG", "three")
    assert r["added"] and not r["replaced"] and db.member_by_bettor("Newbie")["puuid"] == "puuid-3", r
    assert db.count_members() == 3 and db.member("puuid-3")["nickname"] == "three"
    # A Riot ID linked to one account can't be taken by another.
    bets.register("Copycat", "pw1234")
    try:
        tracker.set_riot_id("Copycat", "P3#TAG")
        raise AssertionError("a linked Riot ID should be refused")
    except TrackerError as e:
        assert "linked to the account Newbie" in str(e), str(e)
    # Switching to another Riot ID replaces the entry (the old one leaves the squad, no extra slot needed).
    r = tracker.set_riot_id("Newbie", "P4#TAG")
    assert r["added"] and r["replaced"] and db.member("puuid-3") is None, r
    assert db.member_by_bettor("Newbie")["puuid"] == "puuid-4" and db.count_members() == 3
    # Entering the renamed Riot ID of your own entry just updates it.
    client.names["puuid-4"] = ("Pfour", "X")
    r = tracker.set_riot_id("Newbie", "Pfour#X")
    assert not r["added"] and r["renamed"] and not r["replaced"], r
    assert db.member_by_bettor("Newbie")["name"] == "Pfour" and db.member("puuid-4")["previous_name"] == "P4#TAG"
    # An entry nobody owns (seeded from config.json, or added by the admin) is claimed by entering its Riot ID,
    # and rewards for its games then go to the claiming account.
    assert db.member("puuid-1")["bettor"] is None
    r = tracker.set_riot_id("Copycat", "Pone#NEW")
    assert not r["added"] and db.member("puuid-1")["bettor"] == "Copycat", r
    assert RewardManager(shared.cfg, db).account_name(db.member("puuid-1")) == "Copycat"
    # Leaving, but never below two. (m5, which P4 wasn't in, counts again for the duo P1 and P2.)
    assert tracker.unlink_member("Newbie") == {"removed": 0, "added": 1, "was_active": True} and db.count_members() == 2
    assert db.member_by_bettor("Newbie") is None
    for who, err in (("Copycat", "at least"), ("Nobody", "not in the pool")):
        try:
            tracker.unlink_member(who)
            raise AssertionError(f"{who} should not be able to leave")
        except TrackerError as e:
            assert err in str(e), str(e)

    # The pool can be bigger than the squad: newcomers join the bench, and the line-up is picked from the pool.
    r = tracker.add_member("P3#TAG", "three", active=False)
    assert r["added"] and r["member"]["active"] == 0 and "changes" not in r, r  # the bench changes no games
    assert db.count_members() == 2 and db.count_pool() == 3 and [m["puuid"] for m in db.pool()] == ["puuid-1", "puuid-2", "puuid-3"]
    r = tracker.set_active_list(["puuid-1", "puuid-2", "puuid-3"])
    assert r["changed"] and db.count_members() == 3 and db.count_matches() == 4, (r, db.count_matches())  # P3 has the lines for all four
    assert tracker.set_active_list(["puuid-3", "puuid-1", "puuid-2"])["changed"] is False  # same players: just the order
    assert [m["puuid"] for m in db.members()] == ["puuid-3", "puuid-1", "puuid-2"]
    for bad, err in (([], "at least"), (["puuid-1"], "at least"), (["puuid-1", "puuid-9"], "Not in the pool")):
        try:
            tracker.set_active_list(bad)
            raise AssertionError(f"{bad} should be refused")
        except TrackerError as e:
            assert err in str(e), str(e)
    # Bench P3 again (m5 and the rest still only need P1 and P2), then fill the squad and watch a newcomer
    # take the bench because it's full.
    r = tracker.set_active("puuid-3", False)
    assert r["changed"] and db.count_members() == 2 and db.member("puuid-3")["active"] == 0
    tracker.set_active("puuid-3", True)
    tracker.add_member("Pfour#X", "four")  # P4's current Riot ID (renamed above)
    tracker.add_member("P5#TAG", "five")
    assert db.count_members() == 5
    bets.register("Six", "pw1234")
    r = tracker.set_riot_id("Six", "P6#TAG")
    assert r["added"] and r["active"] is False and db.count_members() == 5 and db.count_pool() == 6, r
    try:
        tracker.set_own_active("Six", True)
        raise AssertionError("joining a full squad should be refused")
    except TrackerError as e:
        assert "full" in str(e)
    try:
        tracker.add_member("Ghost#TAG", active=True)
    except (TrackerError, HenrikError):
        pass
    tracker.set_active("puuid-5", False)
    assert tracker.set_own_active("Six", True)["changed"] and db.member_by_bettor("Six")["active"] == 1
    assert [m["puuid"] for m in db.members()] == ["puuid-1", "puuid-2", "puuid-3", "puuid-4", "puuid-6"]
    # Removing a benched player never touches the games; removing an active one re-derives them.
    r = tracker.remove_member("puuid-5")
    assert r == {"removed": 0, "added": 0, "was_active": False} and db.count_pool() == 5
    r = tracker.unlink_member("Six")
    assert r["was_active"] is True and db.count_members() == 4 and db.member_by_bettor("Six") is None
    assert tracker.sync()["ok"]


@section("house")
def house_giveaways(shared):
    from fivestack.bets import BOOST_MAX_STAKE
    from fivestack.house import JACKPOT_SHARE, MIN_PRIZE, PRIZE_STEP, REFUND_MAX, REFUND_RATE, HouseManager, record
    from fivestack.odds import find_market
    hdb, engine = DB(os.path.join(shared.tmp, "house.db")), OddsEngine(shared.cfg)
    hbets = BetManager(shared.cfg, hdb, engine)
    house = HouseManager(hdb, hbets, RewardManager(shared.cfg, hdb))
    for i, pu in enumerate(PUUIDS.values()):
        hdb.upsert_member(pu, f"P{i + 1}", "TAG", "na", "", None, i)
    assert house.ensure_objectives() == []  # no games and an empty pot: nothing to give

    def line(kills):  # kills: {puuid: kills} (12 for anyone left out)
        return [{"puuid": pu, "agent": "Omen", "kills": kills.get(pu, 12), "deaths": 14, "assists": 4,
                 "score": kills.get(pu, 12) * 250, "damage_dealt": kills.get(pu, 12) * 150, "headshots": 5,
                 "bodyshots": 20, "legshots": 1} for pu in PUUIDS.values()]
    for k in range(8):  # won 13-9, or lost 7-13 every third game
        hdb.insert_match({"match_id": f"h{k}", "map": "Bind", "mode": "competitive", "started_ts": 1000 + k,
                          "rounds_won": 13 if k % 3 else 7, "rounds_lost": 9 if k % 3 else 13,
                          "result": "win" if k % 3 else "loss"},
                         line({pu: 8 + (k * (i + 2)) % 9 for i, pu in enumerate(PUUIDS.values())}))
    assert house.ensure_objectives() == []  # games, but the pot is still empty

    # The pot is the house's estimated take from bets and slots: a lost 20,000 stake priced at a 5% edge is 1,000.
    funder = hdb.insert_bet({"bettor": "Whale", "market_id": "team:win", "market_type": "team_win", "selection": "win",
                             "odds_decimal": 1.9, "stake": 20000, "placed_ts": 1, "context": json.dumps({"fair_prob": 0.5}),
                             "status": "pending"})
    hdb.update_bet(funder, status="lost", payout=0.0, settled_match_id="h0", settled_ts=2)
    summary = house.report()
    assert abs(summary["expected_take"] - 1000) < 0.01 and abs(summary["pot"] - 1000 * (1 - JACKPOT_SHARE)) < 0.01, summary
    assert abs(summary["jackpot"] - 1000 * JACKPOT_SHARE) < 0.01 and summary["given"] == {}, summary

    # A secret set of three for the next game: one personal goal, one squad goal, one bettor goal, hidden until then.
    picks = house.ensure_objectives()
    assert sorted(p["scope"] for p in picks) == ["bettor", "player", "squad"], picks
    assert house.ensure_objectives() == []  # one set at a time
    rows = {r["scope"]: r for r in hdb.query("SELECT * FROM house_objectives WHERE status='open'")}
    assert all(r["prize"] >= MIN_PRIZE and r["prize"] % PRIZE_STEP == 0 and r["drawn_ts"] == 1008 for r in rows.values()), rows
    summary = house.report()
    assert summary["next"]["objectives"] == 3 and not any(r["text"] in json.dumps(summary) for r in rows.values())
    goal = rows["player"]
    assert isinstance(json.loads(goal["params"])["n"], int) and 0 < goal["chance"] < 1 and goal["text"].startswith("P"), goal
    # Fixed goals from here, so the checks don't depend on the draw.
    hdb.execute("UPDATE house_objectives SET kind='kills', target='puuid-1', params=?, text='P1: 12+ kills' WHERE id=?",
                (json.dumps({"n": 12}), rows["player"]["id"]))
    hdb.execute("UPDATE house_objectives SET kind='win', text='Win the game' WHERE id=?", (rows["squad"]["id"],))
    hdb.execute("UPDATE house_objectives SET kind='show_up', target=NULL, text='Have a bet on this game' WHERE id=?",
                (rows["bettor"]["id"],))

    # Two bettors who aren't on the squad bet on the next game: Bea's over misses by one (a bad beat), Cal's loses.
    hbets.register("Bea", "secret1")
    hbets.register("Cal", "secret1")
    over = hbets.place("Bea", "ou:kills:puuid-2", "over", 40, {})
    loss = hbets.place("Cal", "team:win", "loss", 30, {})
    match = {"match_id": "h-next", "map": "Bind", "mode": "competitive", "started_ts": time.time() + 5,
             "rounds_won": 13, "rounds_lost": 7, "result": "win"}
    hdb.insert_match(match, line({"puuid-1": 14, "puuid-2": int(over["line"])}))
    players = hdb.match_players("h-next")
    before = {n: hdb.get_bettor(n)["balance"] for n in ("Bea", "Cal")}
    settled = hbets.settle_for_match(hdb.match("h-next"), players)
    given = house.after_match(hdb.match("h-next"), players, settled)
    refund = min(REFUND_MAX, 40 * REFUND_RATE)
    assert [g for g in given if g["kind"] == "refund"] == [{"bettor": "Bea", "amount": refund, "kind": "refund",
                                                            "bet_id": over["id"], "why": "missed by one"}], given
    assert "Bad beat (missed by one)" in hdb.bet(over["id"])["note"] and hdb.bet(loss["id"])["note"] is None
    done = {r["scope"]: r for r in hdb.query("SELECT * FROM house_objectives WHERE match_id='h-next'")}
    assert {r["status"] for r in done.values()} == {"met"}, done
    assert json.loads(done["player"]["winners"]) == {"P1": rows["player"]["prize"]}
    assert json.loads(done["squad"]["winners"]) == {f"P{i}": rows["squad"]["prize"] / 5 for i in range(1, 6)}
    bettor_share = rows["bettor"]["prize"] / 2
    assert json.loads(done["bettor"]["winners"]) == {"Bea": bettor_share, "Cal": bettor_share}
    assert abs(hdb.get_bettor("Bea")["balance"] - (before["Bea"] + refund + bettor_share)) < 0.01
    assert abs(hdb.get_bettor("Cal")["balance"] - (before["Cal"] + bettor_share)) < 0.01
    revealed = house.for_match("h-next")
    assert len(revealed["objectives"]) == 3 and revealed["refunds"][0]["bettor"] == "Bea", revealed
    # Giveaways are kept out of betting profit, like rewards and slots.
    bea = next(r for r in hbets.leaderboard() if r["name"] == "Bea")
    assert bea["giveaways"] == refund + bettor_share and bea["profit"] == -40, bea
    summary = house.report()
    paid = refund + bettor_share * 2 + rows["player"]["prize"] + rows["squad"]["prize"]
    assert abs(summary["pot"] - (summary["expected_take"] * (1 - JACKPOT_SHARE) - paid)) < 0.01, summary
    assert summary["last"] == revealed["objectives"] and summary["given"]["refund"] == refund, summary
    # A new secret set for the game after, and nothing is paid twice.
    new = hdb.query("SELECT * FROM house_objectives WHERE status='open'")
    assert len(new) == 3 and all(r["after_match"] == "h-next" and r["drawn_ts"] == match["started_ts"] + 1 for r in new)
    assert house.after_match(hdb.match("h-next"), players, settled) == []
    # A surrender carries the set over to the next game.
    hdb.insert_match({"match_id": "h-ff", "map": "Bind", "mode": "competitive", "started_ts": time.time() + 10,
                      "rounds_won": 3, "rounds_lost": 8, "result": "loss"}, line({}))
    house.after_match(hdb.match("h-ff"), hdb.match_players("h-ff"), [])
    assert [r["id"] for r in hdb.query("SELECT * FROM house_objectives WHERE status='open'")] == [r["id"] for r in new]

    # What counts as a bad beat.
    legs = lambda *rs: json.dumps({"legs": [{"result": r} for r in rs]})  # noqa: E731
    assert house.bad_beat({"market_type": "parlay", "context": legs("won", "won", "lost")}, {}) == "one leg short of a 3-leg parlay"
    assert house.bad_beat({"market_type": "parlay", "context": legs("won", "lost")}, {}) is None  # 2 legs: no
    assert house.bad_beat({"market_type": "parlay", "context": legs("won", "lost", "lost")}, {}) is None
    hs = {"market_type": "ou", "line": 24.5, "actual_value": 23.6, "context": json.dumps({"stat": "hs_pct"})}
    assert house.bad_beat(hs, {}) == "missed by 0.9 points" and house.bad_beat({**hs, "actual_value": 22}, {}) is None
    assert house.bad_beat({"market_type": "ou", "line": 16.5, "actual_value": 15, "context": json.dumps({"stat": "kills"})}, {}) is None
    ot = {"mode": "competitive", "rounds_won": 14, "rounds_lost": 12}
    assert house.bad_beat({"market_type": "team_win"}, ot) == "lost in overtime" and house.bad_beat({"market_type": "team_win"}, match) is None
    # Bettor goals: small stakes, long shots, bets on a member.
    won = lambda stake, ctx, mt="ou": {"status": "won", "stake": stake, "odds_decimal": 2, "market_type": mt, "context": json.dumps(ctx)}  # noqa: E731
    assert house._counts({"kind": "small"}, won(25, {})) and not house._counts({"kind": "small"}, won(26, {}))
    assert house._counts({"kind": "underdog"}, won(5, {"fair_prob": 0.3})) and not house._counts({"kind": "underdog"}, won(5, {"fair_prob": 0.4}))
    assert house._counts({"kind": "on_player", "target": "puuid-3"}, won(5, {"puuid": "puuid-3"}))
    assert house._counts({"kind": "on_player", "target": "puuid-3"}, won(5, {"legs": [{"meta": {"puuid": "puuid-3"}}]}, "parlay"))

    # The odds boost of the game: one pick at a better price, the same one until the next game, singles up to a cap.
    BetManager.apply_boost = shared.real_boost
    board = hbets.apply_boost(engine.build(hdb))
    boost = board["boost"]
    market, sel = find_market(board, boost["market_id"], boost["selection"])
    assert sel["decimal"] == hbets.boosted(boost["from_decimal"]) > boost["from_decimal"] and 0.3 <= sel["fair_prob"] <= 0.6
    again = hbets.apply_boost(engine.build(hdb))["boost"]
    assert (again["market_id"], again["selection"], again["decimal"]) == (boost["market_id"], boost["selection"], boost["decimal"])
    try:
        hbets.place("Bea", boost["market_id"], boost["selection"], BOOST_MAX_STAKE + 1, {})
        raise AssertionError("the boost has a stake cap")
    except BetError:
        pass
    boosted = hbets.place("Bea", boost["market_id"], boost["selection"], 20, {})
    assert boosted["odds_decimal"] == boost["decimal"] and json.loads(boosted["context"])["boost"] == boost["from_decimal"]
    assert hdb.get_meta("odds_boost")["after"] == "h-ff"

    # A parlay leg on the same pick prices at the boosted decimal too, under the same stake cap. Paired with a
    # player prop (never a score market), so it can never conflict with whatever market the boost landed on.
    second = ("ou:deaths:puuid-1", "over") if boost["market_id"] == "ou:kills:puuid-1" else ("ou:kills:puuid-1", "over")
    parlay_legs = [{"market_id": boost["market_id"], "selection": boost["selection"]}, {"market_id": second[0], "selection": second[1]}]
    built, _, quote = hbets.quote_parlay(parlay_legs, {})
    boosted_leg = next(l for l in built if l["market_id"] == boost["market_id"])
    other_leg = next(l for l in built if l["market_id"] != boost["market_id"])
    assert boosted_leg["boost"] == boost["from_decimal"] and boosted_leg["odds_decimal"] == boost["decimal"]
    assert not other_leg["boost"]
    try:
        hbets.place_parlay("Bea", parlay_legs, BOOST_MAX_STAKE + 1, {})
        raise AssertionError("a parlay with a boosted leg has the same stake cap")
    except BetError:
        pass
    boosted_parlay = hbets.place_parlay("Bea", parlay_legs, 15, {})
    placed_legs = json.loads(boosted_parlay["context"])["legs"]
    assert next(l for l in placed_legs if l["market_id"] == boost["market_id"])["boost"] == boost["from_decimal"]
    assert boosted_parlay["odds_decimal"] == quote["odds_decimal"]
    hbets.cancel(boosted_parlay["id"], by="Bea")

    # house() backs a boosted parlay leg out of the pot at its pre-boost price, like a boosted single.
    before_house = hbets.house()
    ctx = json.dumps({"legs": [{"odds_decimal": 3.0, "fair_prob": 0.3, "boost": 2.0, "market_type": "ou"},
                               {"odds_decimal": 1.8, "fair_prob": 0.55, "market_type": "team_win"}]})
    dec = round(3.0 * 1.8, 2)
    fake = hdb.insert_bet({"bettor": "Bea", "market_id": "parlay", "market_type": "parlay", "selection": "parlay",
                          "odds_decimal": dec, "stake": 10, "placed_ts": 1, "context": ctx, "status": "pending"})
    hdb.update_bet(fake, status="won", payout=round(10 * dec, 2), settled_match_id="h-ff", settled_ts=3)
    after_house = hbets.house()
    ret = 2.0 * 0.3 * 1.8 * 0.55  # the boosted leg counts at its pre-boost decimal (2.0), not its priced 3.0
    assert abs((after_house["expected_take"] - before_house["expected_take"]) - 10 * (1 - ret)) < 0.01, (before_house, after_house)

    hdb.insert_match({"match_id": "h-later", "map": "Bind", "mode": "competitive", "started_ts": time.time() + 20,
                      "rounds_won": 13, "rounds_lost": 2, "result": "win"}, line({}))
    assert hbets.apply_boost(engine.build(hdb))["boost"] and hdb.get_meta("odds_boost")["after"] == "h-later"  # drawn again

    # The casino's take funds it too: a blackjack round at its edge (even one the house lost) and poker's rake.
    before = house.report()
    with hdb.lock:
        record(hdb.conn, "blackjack", "hand:t1", "Bea", 100, -100, 0.5)
        record(hdb.conn, "poker", "hand:t1", None, 0, 4)
        hdb.conn.commit()
    after = house.report()
    assert abs(after["casino"]["expected_take"] - before["casino"]["expected_take"] - 4.5) < 0.01, after["casino"]
    assert abs(after["pot"] - before["pot"] - 4.5 * (1 - JACKPOT_SHARE)) < 0.01
    assert abs(after["jackpot"] - before["jackpot"] - 4.5 * JACKPOT_SHARE) < 0.01
    assert {g["game"] for g in after["games"]} >= {"blackjack", "poker"}  # the ledger's take per game rides along

    # A season reset keeps the pot (it spans seasons) and clears this season's giveaways from the standings.
    pot = house.report()["pot"]
    hbets.reset()
    assert house.report()["pot"] == pot and house.totals() == {}
    assert all(r["giveaways"] == 0 for r in hbets.leaderboard())
    shared.house_env = types.SimpleNamespace(db=hdb, bets=hbets, house=house, engine=engine, line=line)


@section("wheel")
def daily_wheel(shared):
    import datetime
    from fivestack.bets import TOKEN_BOOST, TOKEN_MAX_STAKE
    from fivestack.odds import find_market
    from fivestack.wheel import SEGMENTS, TOTAL_WEIGHT, WheelManager, next_reset, wheel_day
    env = shared.house_env
    hdb, hbets, house = env.db, env.bets, env.house
    wheel = WheelManager(hdb, house)
    utc = lambda s: datetime.datetime.fromisoformat(s).replace(tzinfo=datetime.timezone.utc).timestamp()  # noqa: E731
    # The day turns over at midnight Pacific (3 AM Eastern), daylight saving time included.
    assert wheel_day(utc("2026-10-01T06:59:00")) == "2026-09-30" and wheel_day(utc("2026-10-01T07:00:00")) == "2026-10-01"
    assert wheel_day(utc("2026-01-15T07:59:00")) == "2026-01-14" and wheel_day(utc("2026-01-15T08:00:00")) == "2026-01-15"
    assert next_reset(utc("2026-03-08T12:00:00")) == utc("2026-03-09T07:00:00")  # the day the clocks go forward
    assert next_reset(utc("2026-11-01T12:00:00")) == utc("2026-11-02T08:00:00")  # and back
    assert TOTAL_WEIGHT == 1000 and SEGMENTS[[s["key"] for s in SEGMENTS].index("jackpot")]["weight"] == 5
    at = {s["key"]: i for i, s in enumerate(SEGMENTS)}
    day = utc("2026-10-02T18:00:00")

    # One spin a day. Credit prizes are free: a giveaway in the standings, but nothing comes out of the pot.
    hbets.register("Wes", "secret1")
    before, pot = hdb.get_bettor("Wes")["balance"], house.pot()
    r = wheel.spin("Wes", now=day, segment=at["c250"])
    assert r["amount"] == 250 and r["spins_left"] == 0 and hdb.get_bettor("Wes")["balance"] == before + 250
    assert abs(house.pot() - pot) < 0.01 and house.totals()["wes"] == 250
    try:
        wheel.spin("Wes", now=day + 3600)
        raise AssertionError("one spin a day")
    except BetError:
        pass
    assert wheel.summary({"name": "Wes"}, now=day)["me"]["spins_left"] == 0
    # "2x respin" gives two more spins the same day; the next day brings a fresh one.
    assert wheel.spin("Wes", now=day + 86400, segment=at["again2"])["spins_left"] == 2
    assert wheel.spin("Wes", now=day + 86400, segment=at["c2000"])["spins_left"] == 1
    bananas = hdb.banana_wallet("Wes")
    last = wheel.spin("Wes", now=day + 86400, segment=at["b100"])
    assert last["amount"] == 100 and last["spins_left"] == 0 and hdb.banana_wallet("Wes") == bananas + 100
    assert [s["amount"] for s in SEGMENTS if s["kind"] == "credits"] == [250, 500, 1000, 2000]
    assert not any(s["kind"] == "nothing" for s in SEGMENTS)
    # A free cosmetic they didn't own, and the jackpot: all of it.
    item = wheel.spin("Wes", now=day + 2 * 86400, segment=at["item"])
    assert hdb.query_one("SELECT price FROM banana_items WHERE bettor='Wes' AND item_id=?", (json.loads(item["detail"])["item_id"],))["price"] == 0
    jackpot = house.report()["jackpot"]
    won = wheel.spin("Wes", now=day + 3 * 86400, segment=at["jackpot"])
    assert jackpot > 1 and won["amount"] == int(jackpot) and 0 <= house.report()["jackpot"] < 1, (jackpot, won)
    assert house.report()["given"]["jackpot"] == int(jackpot)

    # Tokens are used from the bet slip, on the single the bettor picks: a boost token raises its price, an insurance
    # token refunds it if it loses. A bet that doesn't ask for them leaves them be.
    wheel.spin("Wes", now=day + 4 * 86400, segment=at["boost"])
    wheel.spin("Wes", now=day + 5 * 86400, segment=at["insure"])
    assert sorted(p["kind"] for p in wheel.perks("Wes")) == ["boost", "insurance"]
    board = hbets.apply_boost(env.engine.build(hdb))
    pick = "loss" if board["boost"]["market_id"] != "team:win" or board["boost"]["selection"] != "loss" else "win"
    price = find_market(board, "team:win", pick)[1]["decimal"]
    plain = hbets.place("Wes", "team:win", pick, 5, {})
    assert plain["odds_decimal"] == price and "insured" not in json.loads(plain["context"]) and len(wheel.perks("Wes")) == 2
    for tokens, why in (({"boost": True}, "covers a single of up to"), ({"boost": True, "insurance": True}, "covers a single of up to")):
        try:
            hbets.place("Wes", "team:win", pick, TOKEN_MAX_STAKE + 1, {}, tokens=tokens)
            raise AssertionError("the boost token has a stake cap")
        except BetError as e:
            assert why in str(e), e
    assert len(wheel.perks("Wes")) == 2  # a refused bet uses nothing
    bet = hbets.place("Wes", "team:win", pick, TOKEN_MAX_STAKE, {}, tokens={"boost": True, "insurance": True})
    ctx = json.loads(bet["context"])
    assert bet["odds_decimal"] == hbets.boosted(price, TOKEN_BOOST) and ctx["boost"] == price and ctx["insured"]["max"] == TOKEN_MAX_STAKE
    assert wheel.perks("Wes") == [] and hbets.place("Wes", "team:win", pick, 5, {})["odds_decimal"] == price  # used up
    try:
        hbets.place("Wes", "team:win", pick, 5, {}, tokens={"insurance": True})
        raise AssertionError("no token left")
    except BetError as e:
        assert "don't have an insurance token" in str(e), e
    game = {"match_id": "h-wheel", "map": "Bind", "mode": "competitive", "started_ts": time.time() + 30,
            "rounds_won": 13 if pick == "loss" else 2, "rounds_lost": 2 if pick == "loss" else 13,
            "result": "win" if pick == "loss" else "loss"}
    hdb.insert_match(game, env.line({}))
    settled = hbets.settle_for_match(hdb.match("h-wheel"), hdb.match_players("h-wheel"))
    balance, pot = hdb.get_bettor("Wes")["balance"], house.pot()
    given = house.refunds(hdb.match("h-wheel"), settled)
    assert abs(house.pot() - pot) < 0.01  # insurance is free too
    assert [g["kind"] for g in given if g["bettor"] == "Wes"] == ["insurance"] and given[0]["amount"] == TOKEN_MAX_STAKE, given
    assert hdb.get_bettor("Wes")["balance"] == balance + TOKEN_MAX_STAKE and "Insured" in hdb.bet(bet["id"])["note"]
    # A live server ignores a requested slice; demo mode honours it and has no daily limit.
    try:
        wheel.spin("Wes", now=day + 5 * 86400, force="c2000")
        raise AssertionError("still one spin a day")
    except BetError:
        pass
    demo = WheelManager(hdb, house, unlimited=True)
    hbets.register("Dee", "secret1")
    spins = [demo.spin("Dee", now=day, force=key)["prize"] for key in ("again2", "again2", "c250", "nope")]
    assert spins[:3] == ["again2", "again2", "c250"] and spins[3] in at and demo.summary({"name": "Dee"}, now=day)["unlimited"]
    summary = wheel.summary({"name": "Wes"}, now=day + 5 * 86400)
    assert not summary["unlimited"] and len(summary["me"]["history"]) == 8 and summary["recent"][0]["bettor"] == "Dee" and summary["segments"] == SEGMENTS

    # A boost token also works on a leg of a parlay: that leg prices at its boosted price and the parlay's price follows.
    hbets.register("Pat", "secret1")  # their own account: Dee's last spin above was random and may have won a token
    demo.spin("Pat", now=day, force="boost")
    board = hbets.apply_boost(env.engine.build(hdb))
    game = (board["boost"]["market_id"], board["boost"]["selection"])  # the game's boosted pick can't take a token too
    legs = [{"market_id": "team:win", "selection": "loss" if game == ("team:win", "win") else "win"},
            {"market_id": "team:ot", "selection": "no" if game == ("team:ot", "yes") else "yes"}]
    plain, _, plain_quote = hbets.quote_parlay(legs, {})
    boosted_legs = [{**legs[0], "boost": True}, legs[1]]
    built, _, quote = hbets.quote_parlay(boosted_legs, {})
    assert built[0]["boost_token"] and built[0]["boost"] == plain[0]["odds_decimal"], built[0]
    assert built[0]["odds_decimal"] == hbets.boosted(plain[0]["odds_decimal"], TOKEN_BOOST) and built[1]["odds_decimal"] == plain[1]["odds_decimal"]
    assert quote["odds_decimal"] > plain_quote["odds_decimal"], (quote, plain_quote)
    for bad, stake, why in ((boosted_legs, TOKEN_MAX_STAKE + 1, "covers a parlay of up to"),
                            ([{**leg, "boost": True} for leg in legs], 10, "only have 1 boost token")):
        try:
            hbets.place_parlay("Pat", bad, stake, {})
            raise AssertionError(why)
        except BetError as e:
            assert why in str(e), e
    assert len(wheel.perks("Pat")) == 1  # a refused parlay uses nothing
    parlay = hbets.place_parlay("Pat", boosted_legs, 10, {})
    leg = json.loads(parlay["context"])["legs"][0]
    assert parlay["odds_decimal"] == quote["odds_decimal"] and isinstance(leg["boost_token"], int) and wheel.perks("Pat") == []
    assert hdb.query_one("SELECT status, bet_id FROM wheel_perks WHERE id=?", (leg["boost_token"],)) == {"status": "used", "bet_id": parlay["id"]}
    try:
        hbets.place_parlay("Pat", boosted_legs, 10, {})
        raise AssertionError("no token left")
    except BetError as e:
        assert "don't have a boost token" in str(e), e


def main():
    shared = setup()
    for name, check in SECTIONS:
        try:
            check(shared)
        except Exception:
            sys.stdout.flush()
            traceback.print_exc()
            print(f"FAIL: {name}", file=sys.stderr)
            sys.exit(1)
        print(f"ok: {name}")
    bet2 = shared.bet2
    print("selftest OK")
    print(json.dumps({"first_sync": shared.first_sync, "second_sync": shared.second_sync,
                      "tester_balance": round(shared.tester_balance, 2),
                      "sample_line": {"market": bet2["market_id"], "line": bet2["line"], "odds": bet2["odds_decimal"]}},
                     indent=1))


if __name__ == "__main__":
    main()
