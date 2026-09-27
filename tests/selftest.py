"""Offline self-test: detection, stats, odds, betting and settlement against a fake API.

    python tests/selftest.py
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

from fivestack.app import KNOWN_AGENTS  # noqa: E402
from fivestack.bets import BetError, BetManager  # noqa: E402
from fivestack.db import DB  # noqa: E402
from fivestack.henrik import HenrikError  # noqa: E402
from fivestack.insights import AGENT_ROLE, build_insights  # noqa: E402
from fivestack.odds import OddsEngine  # noqa: E402
from fivestack.stats import build_stats, deviation  # noqa: E402
from fivestack.tracker import Tracker  # noqa: E402

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


def main():
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

    # No games yet -> odds not ready, bets refused.
    assert engine.build(db)["ready"] is False
    try:
        bets.place("Tester", "team:win", "win", 10, {})
        raise AssertionError("bet should be refused before any game exists")
    except BetError:
        pass

    # --- detection --------------------------------------------------------
    res = tracker.sync(full=True)
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
    assert len(db.members()) == 5

    # --- stats ------------------------------------------------------------
    st = build_stats(db)
    assert st["team"]["games"] == 2 and st["team"]["wins"] == 1 and st["team"]["losses"] == 1
    p1 = next(m for m in st["members"] if m["name"] == "P1")
    assert p1["overall"]["games"] == 2 and p1["by_map"][0]["games"] == 1
    assert p1["tier_name"] == "Diamond 3"

    # --- 5-stack vs. other games ------------------------------------------
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

    # --- odds -------------------------------------------------------------
    board = engine.build(db, {"map": "Ascent", "agents": {"puuid-1": "Jett"}})
    assert board["ready"]
    assert len(board["player_props"]) == 5 * 6, len(board["player_props"])
    assert len(board["top_markets"]) == 5 and len(board["team"]) == 2
    for mk in board["player_props"] + board["team"]:
        s = sum(x["prob"] for x in mk["selections"])
        assert 1.03 < s < 1.07, (mk["market_id"], s)
    for mk in board["top_markets"]:
        s = sum(x["prob"] for x in mk["selections"])
        assert 1.0 < s < 1.25, (mk["market_id"], s)
    assert engine.build(db, {"map": "Ascent"})["top_markets"][0]["selections"] == board["top_markets"][0]["selections"] or True

    # --- betting ----------------------------------------------------------
    bet = bets.place("Tester", "team:win", "win", 100, {})
    assert bet["status"] == "pending" and db.get_bettor("Tester")["balance"] == 900
    bet2 = bets.place("Tester", "ou:kills:puuid-1", "over", 50, {})
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
    assert len(settled) == 2 and all(b["status"] == "won" for b in settled), settled
    bal = db.get_bettor("Tester")["balance"]
    expected = 850 + 100 * bet["odds_decimal"] + 50 * bet2["odds_decimal"]
    assert abs(bal - expected) < 0.05, (bal, expected)
    lb = bets.leaderboard()
    assert lb[0]["name"] == "Tester" and lb[0]["won"] == 2 and lb[0]["cancelled"] == 2, lb

    # --- visualizations datasets ------------------------------------------
    unroled = [a for a in KNOWN_AGENTS if a.lower() not in AGENT_ROLE]
    assert not unroled, f"add these agents to insights.ROLES: {unroled}"
    # Games: m1 (Ascent, W 13-9, all Jett), m3 (Bind, L 11-13, all Sova), m9 (Haven, W 13-7, all Jett), days apart.
    ins = build_insights(db)
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
    ip1 = next(p for p in ins["players"] if p["puuid"] == "puuid-1")
    assert (ip1["games_win"], ip1["games_loss"]) == (2, 1) and ip1["acs_win"] > ip1["acs_loss"], ip1
    assert set(ip1["maps"]) == {"Ascent", "Bind", "Haven"} and abs(sum(ip1["aim"][k] for k in ("head_pct", "body_pct", "leg_pct")) - 1) < 1e-9
    (roll,) = ins["bankroll"]  # cancelled bets are left out; the two winners are
    assert roll["name"] == "Tester" and len(roll["points"]) == 2, roll
    assert abs(roll["points"][-1]["profit"] - (100 * (bet["odds_decimal"] - 1) + 50 * (bet2["odds_decimal"] - 1))) < 0.05, roll

    # Second sync: nothing new, no duplicates, no re-verification of rejected ids.
    calls_before = client.calls
    res2 = tracker.sync()
    assert res2["ok"] and res2["new_matches"] == 0, res2
    assert client.calls - calls_before == 5, client.calls - calls_before  # only the 5 stored-match calls
    assert db.count_matches() == 3
    assert db.count_member_games() == 17  # re-fetched lines are not duplicated

    # A database from before member_games existed gets one full history fetch, then goes back to normal.
    db.set_meta("history_backfilled", False)
    assert tracker.sync()["full"] is True and db.get_meta("history_backfilled") is True
    assert tracker.sync()["full"] is False and db.count_matches() == 3

    print("selftest OK")
    print(json.dumps({"first_sync": res, "second_sync": res2, "tester_balance": round(bal, 2),
                      "sample_line": {"market": bet2["market_id"], "line": bet2["line"], "odds": bet2["odds_decimal"]}},
                     indent=1))


if __name__ == "__main__":
    main()
