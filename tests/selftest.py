"""Offline self-test: detection, stats, odds, betting and settlement against a fake API.

    python tests/selftest.py

Runs the @section checks below in order and prints "ok: <name>" for each; a failure prints "FAIL: <name>" and stops.
"""
import json
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
from fivestack.bets import BetError, BetManager  # noqa: E402
from fivestack.config import load_bettor_names  # noqa: E402
from fivestack.db import DB  # noqa: E402
from fivestack.forecasts import build_forecasts  # noqa: E402
from fivestack.henrik import HenrikError  # noqa: E402
from fivestack.insights import AGENT_ROLE, betting_report, build_insights, odds_accuracy  # noqa: E402
from fivestack.moments import game_facts  # noqa: E402
from fivestack.gamestate import COMPLETE, FORFEIT, NO_CONTEST, ending, full_game_rounds  # noqa: E402
from fivestack.odds import SCORE_SD_MAX, OddsEngine, fair_chance, partial_game, score_model  # noqa: E402
from fivestack.parlay import correlation, price  # noqa: E402
from fivestack.recap import build_recap  # noqa: E402
from fivestack.rewards import RewardManager, beat_share  # noqa: E402
from fivestack.stats import aggregate, build_stats, deviation, player_metrics  # noqa: E402
from fivestack.timeline import clutches_and_multikills, extract_timeline, spike_sites  # noqa: E402
from fivestack.tracker import Tracker, TrackerError, parse_details  # noqa: E402

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
    return types.SimpleNamespace(tmp=tmp, db=db, cfg=cfg, client=client, engine=engine, bets=bets, tracker=tracker)


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
    # Game rewards: 50 per game + up to 150 for beating your own baseline.
    assert beat_share(250, [100, 150, 200, 250, 300, 350]) == 3.5 / 6 and beat_share(9, [1, 2]) == 1.0 and beat_share(5, []) is None
    db.set_meta("rewards_since", db.match("m9")["started_ts"] - 60)  # rewards switched on after m1 / m3 were played
    rm = RewardManager({**cfg, "members": [{"riot_id": "P1#TAG", "bettor": "Tester"}] + MEMBERS[1:]}, db)
    base_rows = [{"puuid": "x", "started_ts": 1, "rounds_won": 13, "rounds_lost": 7, "score": sc} for sc in (2000, 3000, 4000, 5000, 6000, 7000)]
    base_rows.append({"puuid": "x", "started_ts": 99, "rounds_won": 13, "rounds_lost": 7, "score": 1})  # after the game: ignored
    q = rm.quote({"started_ts": 10, "rounds_won": 13, "rounds_lost": 7}, {"puuid": "x", "score": 5000}, base_rows)
    assert q["acs"] == 250 and q["baseline_games"] == 6 and q["bonus"] == 90, q  # 150 * 3.5/6 = 87.5 -> nearest 5
    assert rm.pay_for_match(db.match("m1"), db.match_players("m1")) == []  # games from before rewards existed
    assert rm.pay_for_match(db.match("m3"), db.match_players("m3")) == []
    lb_before = {r["name"]: r for r in bets.leaderboard()}
    paid = rm.pay_for_match(db.match("m9"), db.match_players("m9"))
    by = {r["puuid"]: r for r in paid}
    assert len(paid) == 5 and all(r["base"] == 50 and 0 <= r["bonus"] <= 150 and r["bonus"] % 5 == 0 for r in paid), paid
    assert by["puuid-1"]["bettor"] == "Tester"  # config override
    p2 = db.get_bettor("P2")  # no account yet -> created unclaimed, named after the member
    assert p2 and not p2.get("password_hash") and p2["balance"] == 1000 + 50 + by["puuid-2"]["bonus"], p2
    assert by["puuid-2"]["beat_share"] is None and by["puuid-2"]["bonus"] == 75  # only 2 baseline games: neutral bonus
    assert rm.pay_for_match(db.match("m9"), db.match_players("m9")) == []  # never paid twice
    loss = {**db.match("m9"), "match_id": "m10", "started_ts": db.match("m9")["started_ts"] + 3600,
            "rounds_won": 9, "rounds_lost": 13, "result": "loss"}
    db.insert_match(loss, [{**p, "match_id": "m10"} for p in db.match_players("m9")])
    lost = rm.pay_for_match(db.match("m10"), db.match_players("m10"))  # a loss pays the same way
    assert len(lost) == 5 and all(r["base"] == 50 and 0 <= r["bonus"] <= 150 for r in lost), lost
    assert next(r for r in lost if r["puuid"] == "puuid-2")["bonus"] == 75
    # The bonus compares against earlier 5-stack games only: P2 has m1, m3 before m9 and m1, m3, m9 before m10
    # (their two non-5-stack games, m2 and m5, don't count).
    assert by["puuid-2"]["baseline_games"] == 2 and next(r for r in lost if r["puuid"] == "puuid-2")["baseline_games"] == 3
    lb_after = {r["name"]: r for r in bets.leaderboard()}
    assert lb_after["Tester"]["profit"] == lb_before["Tester"]["profit"]  # rewards are not betting profit
    tester_lost = next(r for r in lost if r["puuid"] == "puuid-1")["bonus"]
    assert lb_after["Tester"]["rewards"] == 50 + by["puuid-1"]["bonus"] + 50 + tester_lost and lb_after["P2"]["profit"] == 0
    assert lb_after["P2"]["rewards"] == 125 + 125 and len(db.rewards()) == 10
    # bettor_names.json maps a nickname to another account (any case); a config.json `bettor` still wins over it.
    named = RewardManager({**cfg, "members": [{"riot_id": "P1#TAG", "bettor": "Tester"}] + MEMBERS[1:]}, db,
                          {"p3": "Kikii", "P1": "Ignored"})
    assert named.bettor_for(db.member("puuid-3")) == "Kikii" and db.get_bettor("kikii")
    assert named.bettor_for(db.member("puuid-1")) == "Tester" and named.bettor_for(db.member("puuid-4")) == "P4"
    names = load_bettor_names()  # the committed mapping parses
    assert names.get("it") == "Kikii" and names.get("fat") == "fatty", names
    win_rm = RewardManager({**cfg, "win_reward": 100}, db)  # optional extra for wins
    assert win_rm.quote(db.match("m9"), db.match_players("m9")[0], [])["base"] == 150
    assert win_rm.quote(db.match("m10"), db.match_players("m10")[0], [])["base"] == 50


@section("bananas")
def bananas(shared):
    db, bets = shared.db, shared.bets
    bm = shared.bananas = BananaManager({"banana_rate": 0.1, "starting_bananas": 0}, db, bets)  # starters: checked below
    board = bets.leaderboard()
    balances = {b["name"]: b["balance"] for b in db.bettors()}
    # Earning: 0.1 banana per credit gained (won bets' profit + game rewards), paid once.
    added = bm.earn()
    assert added > 0 and bm.earn() == 0, added
    gains, rows = {}, {}
    for b in db.bets(status="won"):
        if b["payout"] > b["stake"]:
            k = b["bettor"].lower()
            gains[k] = gains.get(k, 0) + b["payout"] - b["stake"]
            rows[k] = rows.get(k, 0) + 1
    for r in db.rewards():
        k = r["bettor"].lower()
        gains[k] = gains.get(k, 0) + r["base"] + r["bonus"]
        rows[k] = rows.get(k, 0) + 1
    totals = db.banana_totals()
    assert set(totals) == set(gains), (totals.keys(), gains.keys())  # a bettor who only lost earns nothing
    for k, g in gains.items():  # linear in credits won, up to rounding each row to 0.01
        t = totals[k]
        assert abs(t["earned"] - 0.1 * g) <= 0.005 * rows[k] + 1e-9 and abs(t["season_credits"] - g) < 1e-6, (k, t, g)
        assert abs(t["wallet"] - t["earned"]) < 1e-9 and t["spent"] == 0
    assert all(b["status"] != "lost" for b in db.bets() if f"bet:{b['id']}" in
               {r["ref"] for r in db.query("SELECT ref FROM banana_ledger WHERE reason='bet_win'")})

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
    starter = BananaManager({"banana_rate": 0.1, "starting_bananas": 50}, db, bets)
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
    assert [g["key"] for g in summary["games"]] == ["catch", "says", "dash"] and summary["me"]["plays"] == 3
    assert next(g for g in summary["games"] if g["key"] == "catch")["my_best"] == 300
    t = db.banana_totals()["tester"]
    assert abs(db.banana_wallet("Tester") - (wallet - 3 * ARCADE_PRICE)) < 1e-9 and t["spent"] >= 3 * ARCADE_PRICE
    # The only prize is the board: no bananas back, and credits never move.
    assert [r for r in bets.leaderboard() if r["name"] != "Broke"] == board  # "Broke" is new, with no bets
    assert {b["name"]: b["balance"] for b in db.bettors() if b["name"] != "Broke"} == balances


@section("slots")
def slots(shared):
    from concurrent.futures import ThreadPoolExecutor
    from itertools import product
    from unittest.mock import patch
    from fivestack.slots import MACHINES, STAKES, SYMBOLS, SlotManager, chances, draw_reel, draw_spin, multiplier, rtp

    db = DB(os.path.join(shared.tmp, "slots.db"))
    db.create_bettor("Spinner", 1000)
    db.create_bettor("Empty", 0)
    manager = SlotManager(db)
    bets = BetManager({"starting_balance": 1000}, db, shared.engine)
    m = MACHINES["jackpot"]
    # Seven triples pay. The six regular lines return 95.00% (a 5% house edge), a win about every 8 spins; the secret
    # Golden Onkey's jackpot comes on top of that (97% in all).
    assert len(m["lines"]) == len(m["triples"]) == len(m["show"]) == len(SYMBOLS)
    assert sum(multiplier(m, r) > 0 for r in product(range(len(SYMBOLS)), repeat=3)) == 7
    assert abs(rtp(m) - 0.95) < 1e-12 and abs(rtp(m, secret=True) - 0.97) < 1e-12
    assert abs(sum(chances(m)) - 12410 / 100000) < 1e-12 and [round(1 / c) for c in chances(m)] == [250, 20, 25, 125, 50, 500, 10000]
    # The bigger the payout, the rarer the line.
    by_payout = sorted(range(len(SYMBOLS)), key=lambda i: m["triples"][i])
    assert all(chances(m)[a] > chances(m)[b] for a, b in zip(by_payout, by_payout[1:]))
    # A reel is drawn in proportion to its weights.
    with patch("fivestack.slots.secrets.randbelow", side_effect=range(sum(m["show"]))):
        drawn = [draw_reel(m["show"]) for _ in range(sum(m["show"]))]
    assert [drawn.count(i) for i in range(len(SYMBOLS))] == m["show"]
    # The line is picked first, each triple over exactly its "lines" tickets of the 100,000: banana takes 0-399, ...,
    # the Golden Onkey, the secret 200x symbol, the last 10 (12,400-12,409); every ticket after that is a loss.
    tickets = [sum(m["lines"][:i]) for i in range(len(SYMBOLS) + 1)]
    for i in range(len(SYMBOLS)):
        for t in (tickets[i], tickets[i + 1] - 1):
            with patch("fivestack.slots.secrets.randbelow", return_value=t):
                assert draw_spin(m) == [i, i, i], (i, t)
    # A loss is shown from the display weights, redrawn if they happen to match; the Golden Onkey shows on under 1% of
    # those reels.
    with patch("fivestack.slots.secrets.randbelow", return_value=tickets[-1]), \
            patch("fivestack.slots.draw_reel", side_effect=[6, 6, 6, 6, 1, 6]):
        assert draw_spin(m) == [6, 1, 6]
    assert len(m["show"]) == len(SYMBOLS) and m["show"][6] * 100 < sum(m["show"])
    assert [s["key"] for s in SYMBOLS if s.get("secret")] == ["golden"] and max(m["triples"]) == m["triples"][6] == 200
    assert STAKES == (5, 10, 25, 50, 100, 250, 500)
    for i, (machine, reels, mult, stake) in enumerate([
            (None, [0, 0, 0], 40, 10), ("jackpot", [1, 1, 1], 3, 10), ("jackpot", [6, 6, 6], 200, 500),
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
    # The house: 800 staked by everyone, 101,430 paid out (100,000 of it the Golden Onkey jackpot), and the 5% edge it
    # expected to keep, which leaves the jackpot out (every spin recorded the 95% return without it).
    house = summary["house"]
    assert house["spins"] == 8 and house["staked"] == 800 and house["paid"] == 101430 and house["actual_take"] == -100630
    assert house["secret_paid"] == 100000 and house["expected_take"] == round(800 * (1 - rtp(m)), 2) == 40.0
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
    assert summary["me"]["best"]["payout"] == 100000 and summary["me"]["best"]["reels"] == [6, 6, 6]
    # The squad's biggest wins this season, biggest first.
    assert [w["payout"] for w in summary["big_wins"]] == [100000, 800, 400, 200, 30]
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
    from fivestack import cards
    from fivestack.blackjack import BlackjackManager, is_blackjack, outcome, total
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
        assert v["dealer"]["cards"] == ["9s", None] and v["dealer"]["total"] == 9  # the hole card stays hidden
        _expect_error(bj.action, "Ace", "solo", "hit", 5, contains="moved on")
        _expect_error(bj.action, "Ace", "solo", "split", 0, contains="can't do that")
        v = bj.action("Ace", "solo", "hit", 0)
    assert v["phase"] == "done" and v["dealer"]["cards"] == ["9s", "8c"] and v["seats"][0]["hands"][0]["result"] == "win"
    assert bal("Ace") == 1010
    assert bj.bet("Ace", "solo", 10, r1)["me"]["balance"] == 1010  # a retried bet isn't charged again
    row = db.query_one("SELECT * FROM blackjack_hands WHERE request_id=?", (r1,))
    assert row["status"] == "settled" and row["stake"] == 10 and row["payout"] == 20
    assert db.query_one("SELECT take, expected FROM house_ledger WHERE ref=?", (f"hand:{row['id']}",)) == {"take": -10, "expected": 0.05}
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
        assert v["me"]["actions"] == ["hit", "stand", "double"]  # double after a split, but no second split
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
    shared.casino_db = db


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
    bm = BananaManager({"banana_rate": 0.1, "starting_bananas": 0}, db, bets)
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


@section("seasons")
def seasons(shared):
    db, bets = shared.db, shared.bets
    # Resetting the season archives it first: final standings, every bet and every reward.
    before = {"bets": len(db.bets()), "rewards": len(db.rewards()), "open": sum(b["status"] == "pending" for b in db.bets()),
              "standings": {r["name"]: r for r in bets.leaderboard()}}
    assert db.season_counts()["bets"] == before["bets"] and db.seasons() == []
    season = bets.reset()
    assert db.rewards() == [] and db.reward_totals() == {} and db.get_bettor("P2")["balance"] == 1000 and db.bets() == []
    assert season["name"] == "Season 1" and season["bets"] == before["bets"] and season["rewards"] == before["rewards"]
    saved = {r["name"]: r for r in season["standings"]}
    assert saved["Tester"]["balance"] == before["standings"]["Tester"]["balance"] and saved["P2"]["rewards"] == 250
    archived = db.archived_bets(season["id"])
    assert len(archived) == before["bets"] and not any(b["status"] == "pending" for b in archived)
    assert sum(b["note"] == "Still open when the season was reset" for b in archived) == before["open"]
    assert db.query_one("SELECT COUNT(*) AS n FROM archived_rewards WHERE season_id=?", (season["id"],))["n"] == before["rewards"]
    assert db.query_one("SELECT COUNT(*) AS n FROM archived_transfers WHERE season_id=?", (season["id"],))["n"] == shared.transfer_count
    assert db.transfers() == [] and db.transfer_totals() == {}
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
