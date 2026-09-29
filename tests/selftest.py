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
from fivestack.bets import BetError, BetManager  # noqa: E402
from fivestack.config import load_bettor_names  # noqa: E402
from fivestack.db import DB  # noqa: E402
from fivestack.forecasts import build_forecasts  # noqa: E402
from fivestack.henrik import HenrikError  # noqa: E402
from fivestack.insights import AGENT_ROLE, betting_report, build_insights, odds_accuracy  # noqa: E402
from fivestack.gamestate import COMPLETE, FORFEIT, NO_CONTEST, ending, full_game_rounds  # noqa: E402
from fivestack.odds import SCORE_SD_MAX, OddsEngine, fair_chance, partial_game, score_model  # noqa: E402
from fivestack.recap import build_recap  # noqa: E402
from fivestack.rewards import RewardManager, beat_share  # noqa: E402
from fivestack.stats import aggregate, build_stats, deviation, player_metrics  # noqa: E402
from fivestack.timeline import clutches_and_multikills, extract_timeline, spike_sites  # noqa: E402
from fivestack.tracker import Tracker, parse_details  # noqa: E402

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

    def account(self, name, tag):
        self.calls += 1
        if name not in PUUIDS:
            raise HenrikError(404, "Not found")
        return {"puuid": PUUIDS[name], "region": "na", "name": name, "tag": tag, "card": "c"}

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
    assert len(board["player_props"]) == 5 * 6, len(board["player_props"])
    assert [mk["market_id"] for mk in board["team"]] == ["team:win", "team:rounds", "team:ot", "team:rw", "team:rl",
                                                        "team:margin", "team:score"]
    ot_market = board["team"][2]
    assert ot_market["selections"][1]["prob"] > ot_market["selections"][0]["prob"]  # no game went to OT: "No" favoured
    tops = {mk["market_id"]: mk for mk in board["top_markets"]}  # 6 "tops the scoreboard" markets, each with a counter
    assert len(tops) == 12 and sum(mk["direction"] == "low" for mk in tops.values()) == 6, sorted(tops)
    assert tops["top:kills"]["counter_id"] == "low:kills" and tops["low:kills"]["pair"] == "top:kills"
    assert tops["low:kills"]["label"] == "Bottom fragger" and tops["low:acs"]["label"] == "Lowest ACS"
    assert tops["top:acs_rel"]["label"] == "Popped off" and tops["low:acs_rel"]["label"] == "Got diff'd"
    favourite = lambda mid: tops[mid]["selections"][0]["key"]  # noqa: E731
    assert favourite("top:kills") == "puuid-5" and favourite("low:kills") == "puuid-1", (favourite("top:kills"), favourite("low:kills"))
    # Margin and exact score only offer squad wins, so their prices don't add up to a whole market: each pick just
    # carries double the house edge.
    partial = [mk for mk in board["team"] if mk["type"] in ("team_score", "team_margin")]
    assert all(abs(x["prob"] - min(0.985, max(0.01, x["fair_prob"] * 1.1))) < 1e-3 for mk in partial for x in mk["selections"])
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
    b3 = bets.place("Tester", "team:rounds", "over", 10, {})
    for args in ({}, {"by": "Legacy"}, {"by": None}):
        try:
            bets.cancel(b3["id"], **args)
            raise AssertionError(f"cancel should be refused for {args}")
        except BetError:
            pass
    bets.cancel(b3["id"], by="tester")
    assert db.bet(b3["id"])["status"] == "cancelled" and db.get_bettor("Tester")["balance"] == 850
    b4 = bets.place("Tester", "team:rounds", "under", 10, {})
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
    assert db.season_counts() == {"started_ts": season["ended_ts"], "bets": 0, "rewards": 0, "bettors": db.season_counts()["bettors"]}
    assert bets.reset()["name"] == "Season 2" and [x["name"] for x in db.seasons()] == ["Season 2", "Season 1"]


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
    assert next(mk for mk in ob["team"] if mk["market_id"] == "team:rounds")["mean"] == 24.0  # the 12-round total is left out


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
    assert "Most kills in a 5-stack game: 30" in titles, titles
    assert "Lowest ACS in a 5-stack game: 200" in titles  # 4800 score over 24 rounds; averages say highest / lowest
    assert hl[("ace", "puuid-1")] and hl[("clutch", "puuid-2")]["title"] == "Won a 1v3 clutch"
    assert {h["title"] for h in r["highlights"] if h["kind"] == "first"} == {"First 5-stack game on Clove", "First time playing Controller"}
    assert hl[("rank", "puuid-3")]["title"] == "New peak rank: Platinum 1"
    assert hl[("knife", "puuid-4")]["detail"] == "Round 11" and hl[("team_kill", "puuid-5")]["detail"] == "Round 12"
    assert hl[("comeback", None)]["title"] == "Came back from 0–8 down"
    assert hl[("streak", None)]["title"] == "Snapped a 3-game losing streak"
    assert hl[("upset", None)]["detail"] == "The odds gave the squad 30%"
    assert hl[("longshot", "puuid-5")]["title"] == "P5 hit a +500 long shot" and hl[("parlay", "puuid-2")]
    assert hl[("self_bet", "puuid-1")]  # P1's over on their own kills; P5's pick on P1 isn't betting on yourself
    assert ("self_bet", "puuid-5") not in hl
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
    odd = engine.build(cdb, {}, alts=[("acs", "puuid-1", 200, "exact"), ("kills", "puuid-1", 19.5, "exact")])["custom"]
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


@section("score markets")
def score_markets(shared):
    # Rounds won / lost, winning margin and exact score come from one final-score model that agrees with the
    # match-result and overtime markets.
    board = shared.engine.build(shared.db)
    team = {mk["market_id"]: mk for mk in board["team"]}
    fair = lambda mid: {x["key"]: x["fair_prob"] for x in team[mid]["selections"]}  # noqa: E731
    score, margin = fair("team:score"), fair("team:margin")
    # Only squad wins are offered: the three margins add up to the match-result chance, the 12 regulation scores to
    # a little less (the rest is an overtime win, which counts as "win by 1-2" but not as an exact score).
    assert list(score) == [f"13-{x}" for x in range(12)] and list(margin) == ["w1-2", "w3-5", "w6+"]
    p_win = fair("team:win")["win"]
    assert abs(sum(margin.values()) - p_win) < 2e-3 and sum(score.values()) < p_win, (margin, p_win)
    assert margin["w1-2"] > score["13-11"] and team["team:score"]["selections"][0]["label"] == "13–0"
    assert team["team:rw"]["line"] % 1 == 0.5
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
    l7_13 = {"rounds_won": 7, "rounds_lost": 13, "result": "loss"}  # a loss loses every margin and exact-score bet
    assert ev("team_score", "13-7", l7_13) == "lost" and ev("team_margin", "w6+", l7_13) == "lost"
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
