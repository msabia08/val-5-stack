"""Game rewards: credits paid to each squad member's bettor account for every recorded 5-stack game.

Every game, won or lost, pays each member a flat `game_reward` (plus `win_reward` on top for a win, off by
default) and a performance bonus of up to `performance_bonus_max`, scaled by how their ACS in that game
compares with their own baseline: their previous 5-stack games (every game of theirs in `matches` that started
before this one), so each player is measured against how they usually play with the squad. The bonus is the
share of those baseline games this game beats, so a personal best pays the full bonus and an off night
pays little. With fewer than MIN_BASELINE_GAMES to compare against, the bonus is half the maximum. Bonuses
are rounded to the nearest BONUS_STEP credits.

Only games that start after rewards were switched on pay out (meta `rewards_since`), so the first sync
after an upgrade doesn't pay for the whole backlog of past wins.
"""
import time

from .stats import player_metrics
from .tracker import TrackerError, parse_riot_id

MIN_BASELINE_GAMES = 5
BONUS_STEP = 5  # bonuses are paid in whole multiples of this


def beat_share(acs, baseline):
    """Share of baseline ACS values that `acs` beats; a tie counts as half."""
    if not baseline:
        return None
    below = sum(1 for b in baseline if b < acs)
    ties = sum(1 for b in baseline if b == acs)
    return (below + 0.5 * ties) / len(baseline)


class RewardManager:
    def __init__(self, cfg, db, bettor_names=None):
        self.db = db
        self.game = float(cfg.get("game_reward", 250))
        self.win = float(cfg.get("win_reward", 0))
        self.bonus_max = float(cfg.get("performance_bonus_max", 250))
        self.starting = float(cfg.get("starting_balance", 1000))
        self.names = {k.lower(): v for k, v in (bettor_names or {}).items()}  # nickname -> account (bettor_names.json)
        self.overrides = {}  # (name, tag) lower-cased -> bettor account name from config members[].bettor
        for entry in cfg.get("members") or []:
            if isinstance(entry, dict) and (entry.get("bettor") or "").strip():
                try:
                    name, tag = parse_riot_id(entry.get("riot_id") or entry.get("id") or "")
                except TrackerError:
                    continue
                self.overrides[(name.lower(), tag.lower())] = entry["bettor"].strip()
        self.since = db.get_meta("rewards_since")
        if self.since is None:
            self.since = time.time()
            db.set_meta("rewards_since", self.since)

    @property
    def enabled(self):
        return self.game > 0 or self.win > 0 or self.bonus_max > 0

    def bettor_for(self, member):
        """The member's bettor account, created unclaimed if missing. First match wins: `bettor` on the member in
        config.json, then bettor_names.json keyed by nickname, then the nickname (or Riot name) itself."""
        nickname = member.get("nickname") or member["name"]
        name = (self.overrides.get((member["name"].lower(), member["tag"].lower()))
                or self.names.get(nickname.lower()) or nickname)
        name = name.strip()[:32]
        b = self.db.get_bettor(name)
        if not b:
            self.db.create_bettor(name, self.starting)
            b = self.db.get_bettor(name)
        return b["name"]

    def quote(self, match, player, baseline_rows):
        """The reward one member earns for a game, without paying it."""
        rounds = (match.get("rounds_won") or 0) + (match.get("rounds_lost") or 0)
        acs = player_metrics(player, rounds)["acs"]
        started = match.get("started_ts") or 0
        baseline = [
            player_metrics(r, (r.get("rounds_won") or 0) + (r.get("rounds_lost") or 0))["acs"]
            for r in baseline_rows
            if r["puuid"] == player["puuid"] and (r.get("started_ts") or 0) < started
            and (r.get("rounds_won") or 0) + (r.get("rounds_lost") or 0) > 0
        ]
        share = beat_share(acs, baseline) if len(baseline) >= MIN_BASELINE_GAMES else None
        bonus = self.bonus_max * (share if share is not None else 0.5)
        base = self.game + (self.win if match.get("result") == "win" else 0.0)
        bonus = int(bonus / BONUS_STEP + 0.5) * BONUS_STEP  # halves round up
        return {"base": base, "bonus": bonus, "acs": round(acs, 1),
                "beat_share": share, "baseline_games": len(baseline)}

    def pay_for_match(self, match, players):
        """Pay every member for a game: the flat game reward (plus any win reward) and the performance bonus.
        Returns the rewards paid (none for games from before rewards were switched on)."""
        if not self.enabled or (match.get("started_ts") or 0) < self.since:
            return []
        members = {m["puuid"]: m for m in self.db.members()}
        baseline_rows = self.db.player_rows()  # 5-stack games only; quote() keeps the ones before this match
        paid = []
        for p in players:
            member = members.get(p["puuid"])
            if not member:
                continue
            reward = {"match_id": match["match_id"], "puuid": p["puuid"], "bettor": self.bettor_for(member),
                      **self.quote(match, p, baseline_rows)}
            if self.db.pay_reward(reward):
                paid.append(reward)
        return paid
