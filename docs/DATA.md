# Data reference

Everything the 5-Stack Tracker can know about a game, in three layers:

1. **[HenrikDev API](#1-henrikdev-api)**: what the unofficial Valorant API returns (the source of everything).
2. **[Our database](#2-our-database-datatrackerdb)**: what the tracker keeps from it, in `data/tracker.db`.
3. **[Our web API](#3-our-web-api)**: what the website reads from the server (`/api/*`).

Plus **[what's available but not used yet](#4-available-but-not-used-yet)**, as a menu for future features.

The HenrikDev structures below were captured from real responses on 2026-09-27 (region `na`, platform `pc`)
with [`scripts/inspect_henrik.py`](../scripts/inspect_henrik.py); re-run it to refresh them. Names, tags and IDs
are never captured, only field names, types and a few harmless sample values.

---

## 1. HenrikDev API

Base URL `https://api.henrikdev.xyz`, authenticated with an `Authorization: <api key>` header. A Basic key allows
about 30 requests a minute; the tracker spaces requests `min_request_interval_s` apart (1.5 s) and backs off on 429.

| Endpoint | Used by the tracker | What it's for |
|---|---|---|
| `GET /valorant/v2/account/{name}/{tag}` | yes, once per member | Riot ID → PUUID |
| `GET /valorant/v1/by-puuid/stored-matches/{region}/{puuid}` | yes, every sync, one call per member | Each member's own line for every stored game; 5-stack detection and the non-5-stack baseline |
| `GET /valorant/v4/match/{region}/{match_id}` | yes, up to `details_per_sync` per sync | Full record of one game: all 10 players, rounds, kills; verifies 5-stacks, adds ranks and game length, feeds the round timelines |
| `GET /valorant/v4/by-puuid/matches/{region}/{platform}/{puuid}` | no | The same full records for a player's most recent games |
| `GET /valorant/v3/by-puuid/mmr/{region}/{platform}/{puuid}` | no | Current rank, RR, peak rank, and every past act's result |
| `GET /valorant/v2/by-puuid/mmr-history/{region}/{platform}/{puuid}` | no | RR gained or lost in each recent competitive game |
| `GET /valorant/v2/by-puuid/stored-mmr-history/{region}/{platform}/{puuid}` | no | The same, from HenrikDev's store, with paging |

Field notation in the trees: `[]` is "each item of the list"; `A | B` means the field can be either (usually
`NoneType | dict` for something that's only there sometimes); lengths are what the sample returned.

### Account (v2)

`GET /valorant/v2/account/{name}/{tag}`

```
data: dict
  puuid: str                 the stable player ID everything else is keyed on
  region: str                e.g. 'na'
  account_level: int
  name: str
  tag: str
  card: str                  player card ID
  title: str                 player title ID
  platforms: list[str]
  updated_at: str
```

### Stored matches (v1)

`GET /valorant/v1/by-puuid/stored-matches/{region}/{puuid}` with optional `mode`, `map`, `size`, `page`.
One entry per stored game, **only the requesting player's own line**. Leaving out `size` returns everything
stored. HenrikDev's store can have holes (a game nobody ever asked the API about may be missing), which is why
the tracker double-checks a game that's missing for one member against the full record.

```
results: dict                total, returned, before, after (counts of stored records, not all games played)
data: list
  []: dict
    meta: dict
      id: str                match ID
      map: {id, name}
      version: str
      mode: str              e.g. 'Competitive', 'Unrated', 'Deathmatch', 'Team Deathmatch', 'Gauntlet: Glitched'
      started_at: str        ISO time
      season: {id, short}    short e.g. 'e11a5'
      region: str
      cluster: str           server, e.g. 'US Central (Illinois)'
    stats: dict              this player's line only
      puuid, name, tag: str
      team: str              'Red' / 'Blue' (in free-for-all modes it's the player's own ID or 'Team_N')
      level: int
      character: {id, name}  agent
      tier: int              competitive tier number
      score: int             total combat score for the game (divide by rounds for ACS)
      kills, deaths, assists: int
      shots: {head, body, leg}
      damage: {made, received}
    teams: dict
      red: int               rounds won by Red
      blue: int              rounds won by Blue
```

### Full match record (v4)

`GET /valorant/v4/match/{region}/{match_id}` returns `data` with this shape;
`GET /valorant/v4/by-puuid/matches/{region}/{platform}/{puuid}` returns a list of them (`?size=N`).

```
metadata: dict
  match_id: str
  map: {id, name}
  game_version: str
  game_length_in_ms: int
  started_at: str
  is_completed: bool
  queue: {id, name, mode_type}      mode_type e.g. 'Standard'
  season: {id, short}
  platform: str                     'pc'
  premier: NoneType                 Premier match info when it is one
  party_rr_penaltys: list
    []: {party_id, penalty}
  region: str
  cluster: str

players: list (10)
  []: dict
    puuid, name, tag: str
    team_id: str                    'Red' / 'Blue'
    platform: str
    party_id: str                   same value for players who queued together
    agent: {id, name}
    stats: dict
      score, kills, deaths, assists: int
      headshots, bodyshots, legshots: int
      damage: {dealt, received}
    ability_casts: {grenade, ability1, ability2, ultimate}      whole-game counts
    tier: {id, name}                rank at the time, e.g. 'Diamond 2'
    customization: {card, title, preferred_level_border}
    account_level: int
    session_playtime_in_ms: int
    behavior: dict
      afk_rounds: float
      friendly_fire: {incoming, outgoing}
      rounds_in_spawn: float
    economy: dict
      spent: {overall, average}
      loadout_value: {overall, average}

observers: list                     empty outside custom/tournament games
coaches: list

teams: list (2)
  []: dict
    team_id: str                    'Red' / 'Blue'
    rounds: {won, lost}
    won: bool                       the official winner (decides a surrender)
    premier_roster: NoneType

rounds: list (one per round, 13-22 in the sample)
  []: dict
    id: int                         0-based, matches kills[].round
    result: str                     how the round ended, e.g. 'Elimination', 'Defuse'
    ceremony: str                   Riot's round tag, e.g. 'CeremonyDefault', 'CeremonyCloser', 'CeremonyThrifty',
                                    'CeremonyFlawless', 'CeremonyAce', 'CeremonyClutch'
    winning_team: str               'Red' / 'Blue'
    plant: NoneType | dict          only when the spike was planted
      round_time_in_ms: int
      site: str                     'A' / 'B' / 'C'
      location: {x, y}
      player: {puuid, name, tag, team}
      player_locations: list        everyone alive at the plant
        []: {player: {puuid, name, tag, team}, view_radians, location: {x, y}}
    defuse: NoneType | dict         only when the spike was defused; same shape as plant, without site
    stats: list (10)                every player's round
      []: dict
        player: {puuid, name, tag, team}
        ability_casts: {grenade, ability_1, ability_2, ultimate}    empty (None) in practice
        damage_events: list         who they damaged this round
          []: {player: {puuid, name, tag, team}, headshots, bodyshots, legshots, damage}
        stats: {score, kills, headshots, bodyshots, legshots}
        economy: dict
          loadout_value: int        value of what they carried into the round
          remaining: int            credits left after buying
          weapon: {id, name, type}
          armor: NoneType | {id, name}
        was_afk, received_penalty, stayed_in_spawn: bool

kills: list (every kill in the game, 94-158 in the sample)
  []: dict
    time_in_round_in_ms: int
    time_in_match_in_ms: int
    round: int                      0-based round index
    killer: {puuid, name, tag, team}
    victim: {puuid, name, tag, team}
    assistants: list
      []: {puuid, name, tag, team}
    location: {x, y}                where the victim died (map coordinates)
    weapon: dict
      id: str
      name: NoneType | str          empty for some non-gun kills
      type: str                     'Weapon', 'Ability', 'Bomb' (spike detonation), 'Fall'
    secondary_fire_mode: bool       e.g. scoped / alt-fire
    player_locations: list          everyone else alive at that moment
      []: {player: {puuid, name, tag, team}, view_radians, location: {x, y}}
```

### Current rank (v3 MMR)

`GET /valorant/v3/by-puuid/mmr/{region}/{platform}/{puuid}`

```
account: {name, tag, puuid}
peak: dict
  season: {id, short}
  ranking_schema: str
  tier: {id, name}
  rr: int
current: dict
  tier: {id, name}
  rr: int                           0-100 within the tier
  last_change: int                  RR from the most recent game
  elo: int                          tier and RR as one number
  games_needed_for_rating: int
  rank_protection_shields: int
  leaderboard_placement: {rank, updated_at}
seasonal: list (one per act played)
  []: dict
    season: {id, short}
    wins, games: int
    end_tier: {id, name}
    end_rr: int
    ranking_schema: str
    leaderboard_placement: NoneType | {rank, updated_at}
    act_wins: list                  the act-rank badge triangles
      []: {id, name}
```

### Rank / RR history (v2 MMR history, and stored MMR history)

`GET /valorant/v2/by-puuid/mmr-history/{region}/{platform}/{puuid}` (recent games, in `data.history`) and
`GET /valorant/v2/by-puuid/stored-mmr-history/{region}/{platform}/{puuid}` (HenrikDev's store, in `data`, with
`results` counts and `size`/`page` paging). Each entry:

```
[]: dict
  match_id: str                     joins to our matches table
  map: {id, name}
  season: {id, short}
  tier: {id, name}                  rank after the game
  rr: int                           RR after the game
  last_change: int                  RR gained (+) or lost (-) in that game
  elo: int
  refunded_rr: int
  was_derank_protected: bool
  date: str
```

---

## 2. Our database (`data/tracker.db`)

SQLite, created by `fivestack/db.py`. Tables are only ever created, never migrated, so a new column won't reach
an existing database without migration code (new tables are fine).

| Table | One row per | Filled by | Key columns |
|---|---|---|---|
| `members` | squad member | tracker (account lookup) | `puuid`, `name`, `tag`, `region`, `nickname`, `card`, `order_index` |
| `matches` | 5-stack game | tracker | `match_id`, `map`, `mode`/`mode_label`, `started_at`/`started_ts`, `season`, `region`, `team`, `rounds_won`, `rounds_lost`, `result` (`win`/`loss`/`draw`), `game_length_ms`, `source` (`stored`/`details`/`demo`), `party_verified`, `details_fetched` |
| `match_players` | member in a 5-stack game | tracker | `match_id`, `puuid`, `agent`, `score`, `kills`, `deaths`, `assists`, `headshots`, `bodyshots`, `legshots`, `damage_dealt`, `damage_received`, `tier`, `tier_name` |
| `member_games` | member's line in **any** stored game in a tracked mode | tracker | same stats as `match_players`, plus the game's `map`, `mode`, `started_ts`, rounds and `result`. Rows whose `match_id` isn't in `matches` are that member's non-5-stack baseline |
| `match_timelines` | 5-stack game | tracker (from the v4 record) | `match_id`, `data` (JSON, see below; NULL if the record had no round data), `fetched_ts`. The round charts, recaps and the team "moment" markets (`moments.game_facts`) read it |
| `bettors` | betting account | sign-up, rewards, demo | `name`, `balance`, `created_at`, `salt`, `password_hash` (NULL = unclaimed) |
| `bets` | bet (single or parlay) | bet slip, settlement | `bettor`, `market_id`, `market_type` (`ou`/`exact`/`top`/`team_win`/`team_ou`/`team_ot`/`team_rw`/`team_rl`/`team_margin`/`team_score`/`team_moment`/`parlay`; `team_ou`, `team_rw` and `team_rl` are no longer offered but still settle), `description`, `selection`, `line`, `odds_decimal`, `stake`, `placed_ts`, `context` (JSON: stat, player, direction, `fair_prob` (the model's chance before the house edge, on bets placed since the odds accuracy card), `custom` (a custom line), or a parlay's `legs`, each with its own `fair_prob` and `hist` (the leg settled at its line on the last 60 games, newest first: `1` won, `0` lost, `-` void), plus `corr` (the price: `odds_decimal`, `independent_decimal`, `factor`, `games`, `linked`)), `status` (`pending`/`won`/`lost`/`void`/`cancelled`), `settled_match_id`, `settled_ts`, `payout`, `actual_value`, `note` |
| `rewards` | member per game | reward manager | `match_id`, `puuid`, `bettor`, `base`, `bonus`, `acs`, `beat_share`, `baseline_games`, `created_ts` |
| `seasons` | ended betting season | season reset | `id`, `name` ('Season N'), `started_ts`, `ended_ts`, `standings` (JSON: the leaderboard at the end), `bets`, `rewards` (counts) |
| `archived_bets` | bet from an ended season | season reset | `season_id` plus every `bets` column; bets still open at the reset are archived as `cancelled` |
| `archived_rewards` | reward from an ended season | season reset | `season_id` plus every `rewards` column |
| `banana_ledger` | change to a bettor's bananas | `bananas.py` (earning after settlement and at start-up, buying), season reset, demo | `bettor`, `delta` (+ earned, − spent), `reason` (`bet_win`, `reward`, `starter` (the season's starting bananas, ref `starter:<season number>:<name>`), `purchase`, `prank`, `arcade`, `season_reset`, `demo`, or `test` in the self-test), `ref` (`bet:<id>`, `reward:<match>:<puuid>`, `<item>:<ns>`, `season:<id>:<name>`; unique with `reason`, so nothing is paid twice), `credits` (the credit gain an earning came from), `note`, `created_ts`. A wallet is the sum of its rows |
| `banana_items` | item a bettor owns | Onkey's Shop | `bettor`, `item_id` (see `bananas.CATALOG`), `price`, `bought_ts`; kept through season resets |
| `banana_equipped` | worn slot | Onkey's Shop | `bettor`, `slot` (`name_color`/`badge`/`title`/`banner`/`ticket`/`celebration`/`theme`), `item_id` |
| `banana_pranks` | social item used on someone | Onkey's Shop | `item_id` (see `bananas.SOCIAL`: `sc-peel`, `sc-jinx`, `sc-upside`, `sc-shrink`, `sc-fog`, `sc-clown`, `sc-glitter`, `sc-bounty`, `sc-heckle`, `sc-nick`, `sc-note`, `sc-title`), `bettor` (who sent it), `target`, `text` (a note or title), `price`, `created_ts`, `expires_ts`, `games` (when above 0, also over once that many 5-stack games have started after `created_ts`: 3 for most pranks) |
| `arcade_plays` | paid play in Onkey's Arcade | `arcade.py` | `bettor`, `game` (`catch`/`says`/`dash`), `token` (one-time, unique), `price`, `started_ts`, `finished_ts`, `score` (NULL until the game sends it back; boards use each bettor's best above 0). The price is a `banana_ledger` row with reason `arcade` and ref `play:<token>` |
| `slot_spins` | immediately settled slot spin | `slots.py` | `id`, `bettor`, `machine` (`jackpot`; retired `classic` rows retained), `stake`, `reels` (JSON array of three symbol indexes), `multiplier`, `payout` (stake included), `created_ts`, `request_id` (unique per bettor), `season_id` (NULL for current season; set at reset). Balance update and insert are one transaction. Old rows and retry keys survive resets |
| `transfers` | credits one bettor sent another | Send credits (`BetManager.send`), settlement (the tax) | `id`, `sender`, `recipient`, `amount`, `note` (NULL if none), `created_ts`; the generosity tax: `tax_status` (`open` = waiting on the recipient's next winning bet, `paid`, NULL = this transfer earned none), `tax_amount`, `tax_bet_id` (the bet it came from), `tax_match_id`, `tax_ts` |
| `archived_transfers` | transfer from an ended season | season reset | `season_id` plus every `transfers` column |
| `meta` | setting | various | JSON values: `cookie_secret`, `rejected_matches`, `last_sync`, `backfilled`, `history_backfilled`, `rewards_since`, `season_started` |

`match_timelines.data` is a compact copy of the v4 record (see `fivestack/timeline.py`):

```
our_team: str                       'Red' / 'Blue'
team_of: {puuid: team}              all 10 players
rounds: list
  []: {winner, site (NoneType | str), planter_team (NoneType | str), defused: bool}
kills: list
  []: {r (round, 0-based), t (ms into the round), killer, victim (puuids), weapon (NoneType | str)}
```

---

## 3. Our web API

Served by `fivestack/app.py`. Everything except `/login`, `/logout` and `/style.css` needs the site password
cookie when `site_password` is set. Bettor actions also need the bettor session cookie.

| Method and path | Returns / does |
|---|---|
| `GET /api/status` | Config state, squad members (with their bettor account), record, tracker and sync state, rate limit, reward settings, generosity tax settings (`tax_rate`, `tax_min_transfer`), tunnel, log |
| `GET /api/stats` | Players page data: per member overall, per agent, per map, form (last 15 games), best games, `range` (highest and lowest complete game for ACS, K/D, kills, deaths, assists, ADR and HS%), 5-stack vs other games; and `timeline`: every complete game oldest first (`games`) with each member's per-game values of those stats lined up with it (`series[puuid][stat]`), for the trend chart |
| `GET /api/forecasts?stat=<key>&player=<puuid>&map=&agent=` | Forecasts tab data for one stat (`kills`, `deaths`, `assists`, `kpr`, `dpr`, `apr`, `acs`, `adr`, `hs_pct`; default `acs`; 400 for anything else): every player's `forecast_games` count, and for one player (default the first with forecasts) each forecast game (`range` = [low, high], `typical` = median, `expected` = mean, `actual`), `next` (the forecast for their next game from all their games so far, on `map` as `agent` when given, the odds board's context: `range`, `typical`, `expected`, `map`, `agent`, `games` used; null before 5 games), the `overall` record and the map × role `cells` (averages of those, plus above / inside / below counts and agents) with `best` / `worst` |
| `GET /api/insights` | Visualizations tab data: games (each with its `damage_share` and `damage_cum`, the running all-time share), moments, sessions, maps, players (maps, aim, agents), comps, round insights (clutches, multi-kills, spike sites) |
| `GET /api/betting-report` | The Standings page's betting cards: ROI by market type per bettor, bets on yourself vs others, each bettor's profit over time (`bankroll`), and the odds accuracy card (`accuracy`: picks, won, expected wins and a verdict overall, per market type and per chance bin) |
| `GET /api/content` | Known maps and agents, and which agents each member plays (the page no longer uses it: the Place bets map and agent pickers were removed) |
| `GET /api/matches?limit=` | 5-stack games newest first, each with its players and `ending` (`complete`/`forfeit`) |
| `GET /api/match/{id}` | One game with its players |
| `GET /api/recap?match=<id>` | Matches tab recap for one game (the latest if `match` is missing or unknown; `null` with no games): `match` header (with `night_game`, `night_record`, `number`, `older` / `newer` ids), `players` (this game's line, `usual` from earlier complete games, `forecast` for kills / deaths / ACS, `rounds` totals), `rounds` (per round: `won`, running `score`, `side`, spike, `first_blood`, `multi`, `clutch`), `betting`, `rewards` and ranked `highlights` ({`score`, `kind`, `title`, `detail`, `puuid`, `tone`}) |
| `GET /api/odds?map=&agents=` | The odds board: team markets (`team:win`, whose `basis.recent` is the last five results newest first; `team:ot`; `team:margin` and `team:score` covering every result, losses and overtime included; and, once 5 games have a timeline, the round "moment" markets `team:pistol` / `team:half` / `team:ace` / `team:comeback` (yes / no) and `team:flawless` (over / under), `type: "team_moment"` with the fact in `fact`; no total-rounds or rounds won / lost markets any more), player props (over / under on kills, deaths, assists and HS% only, `odds.PROP_KEYS`; `stat_defs` lists those four), top/bottom-of-scoreboard markets. The page never sends `map` or `agents` now. A selection that would have won the last 3+ games in a row at today's line carries `streak` (the run length, counted back through `streak_lookback` games; voids skipped), shown as a flame border; a roughly 50/50 pick (fair chance 35–65%) in a market with three or more picks that lost as many carries `cold` instead, shown frosted (two-way markets never do: the other side already has the flame) |
| `GET /api/odds/custom?puuid=&stat=&line=&map=&agents=` (or `&exact=N` instead of `line`) | A custom line's `market` (id `alt:<stat>:<puuid>:<line>`; an exact number's is `exact:<stat>:<puuid>:<N>`, type `exact`, one `exact` selection needing a 2% chance, `limits.exactly`): the reasonable `limits` for "at least" / "at most", `typical`, and over / under `selections`, each with `available` (fair chance between 5% and 90%) or a `reason`; 400 for a bad line, player or stat |
| `GET /api/bets?status=&bettor=&limit=` | Bets newest first, with the game each settled on (`game_map`, `game_rounds_won`, ...) |
| `GET /api/bettors` | Leaderboard: balance, match-betting-only profit, rewards, `transfers` (net credits received from other bettors), `slots` (current season slot payouts minus stakes, excluded from profit and ROI), record, ROI, open stakes |
| `GET /api/rewards?limit=` | Recent game rewards |
| `GET /api/transfers?bettor=&limit=` | This season's transfers newest first (default 50), optionally only those a bettor sent or received; a paid tax also carries `tax_bet_description` and `tax_bet_net` (the taxed bet's net winnings) |
| `POST /api/transfers` | Send credits as the signed-in bettor: `to`, `amount` (at least 1, at most the balance, rounded to cents), optional `note` (80 characters); returns the `transfer`, the sender's new `bettor` and the leaderboard |
| `GET /api/bettor/me` | The signed-in bettor, if any: `name`, `balance` and `bananas` (the top bar's credits and bananas chips, and the bet slip), `open_bets` and `open_stake` (the bet slip and the Overview), and `recent_wins` (their last 20 won bets, newest settled first: `id`, `description`, `market_type`, `stake`, `odds_decimal`, `payout`, `settled_ts`), which the page celebrates with confetti, `recent_received` (the last 20 transfers other bettors sent them, newest first), which it announces once, and `recent_taxes` (the last 20 generosity taxes they collected, newest paid first, with the bet's `bet_description`), which it also announces once |
| `POST /api/bettor/register`, `/login`, `/logout`, `/password` | Bettor accounts |
| `POST /api/bettor/clear-password` | Admin: free a bettor name |
| `POST /api/odds/parlay` | Price a parlay without placing it (`legs`, `context`): `odds_decimal` (what it would pay), `independent_decimal` (the legs' odds multiplied), `factor` (the cut for linked legs, 1 = none), `games` (games replayed), `linked` (groups of leg indexes that won together more than chance) and `legs`; 400 with the reason when the legs can't share a parlay |
| `POST /api/bets` | Place a single (`market_id`, `selection`, `stake`) or a parlay (`legs`, `stake`) |
| `DELETE /api/bets/{id}` | Cancel a pending bet |
| `GET /api/seasons` | The current season (start, bet, reward and transfer counts) and every past season with its final standings |
| `POST /api/bettors/reset` | End the season: needs `{"confirm": "RESET"}` (and the admin password header if one is set); archives, then resets |
| `GET /api/shop` | Onkey's Shop: `rate` (bananas per credit gained), `slots`, `catalog` (each item's `id`, `slot`, `name`, `price`, `desc` and `look`: what the page draws, e.g. `cls`, `emoji`, `text`, `colors`, `theme`), `social` (items used on someone else, with `hours`, `games`, `max_len`), and `me` for a signed-in bettor: `wallet`, `earned`, `season_earned`, `season_credits` (the credits those came from), `spent`, `owned`, `worn` ({slot: item id}) and `history` (their last 25 ledger rows) |
| `POST /api/shop/buy` | Buy an item (`{"item": id}`, worn straight away) or use a social one (`{"item", "target", "text"}`); returns `item`, `wallet` and the updated `shop`. Needs the bettor session |
| `POST /api/shop/equip` | Wear an owned item (`{"slot", "item"}`) or take a slot off (`"item": ""`); returns `worn` and `shop` |
| `GET /api/troop` | The Monkeys tab: every bettor with `wallet`, `earned`, `season_earned`, `season_credits`, `spent`, `items`, `collection` (bananas' worth of items owned), `credits` and betting `profit`, most collected first; plus `looks` ({lower-cased name: {`worn`: {slot: look}, `pranks`: [active pranks on them, with `games_left`]}}), which the page uses to style names everywhere |
| `GET /api/troop/profile?name=` | One bettor's profile on the Monkeys tab: the same fields, `owned` items, `worn`, `pranks` received (each with `active`), `pranks_sent`, `betting` (balance, profit, rewards, record, ROI) and `looks`; 404 for an unknown name |
| `GET /api/arcade` | Onkey's Arcade: `price`, `games` (each `key`, `name`, `icon`, `desc`, `plays`, `board`: the top 10 bettors' best scores, and the signed-in bettor's `my_best`) and `me` (`wallet`, `plays`) |
| `GET /api/slots` | `machines` (one entry, key `jackpot`, name `Slots`, `triples` 8/12/20/30/50/80, `pair: 0`, theoretical return percentage `rtp: 92.59`), six equally likely `symbols` (`key`, `icon`, `name`), `stakes` (5, 10, 25, 50, 100), and signed-in `me` (`name`, season `spins`, `staked`, `returned`, `net`) and `history` (last ten current-season spins, parsed reels and net). Signed out: `me: null`, empty history |
| `POST /api/slots/spin` | Needs a bettor session. Body: numeric `stake` from the allowed list, `request_id` (16–80 ASCII letters/digits/hyphens/underscores), optional `machine` (defaults to `jackpot`). Server draws three independent symbols and atomically pays the result. Returns `spin` and current `balance`. Reusing the reference returns that spin without charging, including saved retired-machine results; new retired-machine spins are rejected. A different machine or stake with the same reference is rejected. 403 signed out, 400 invalid inputs or insufficient credits |
| `POST /api/arcade/start` | Pay for a play (`{"game": key}`): takes 5 bananas, returns `token` and the new `wallet`. Needs the bettor session |
| `POST /api/arcade/finish` | Record a play's score (`{"token", "score"}`): once, by the bettor who paid, within an hour, and no higher than the game allows for the time it ran; returns `score`, `best_before`, `new_best`, `rank`, `champion` and the game's `board` |
| `POST /api/sync` | Start a sync (`{"full": true}` for a full history re-scan) |

---

## 4. Available but not used yet

Things the API already returns that no feature uses. The first group needs no extra API calls (it's in the v4
record the tracker already fetches; only the compact timeline would need to keep more of it).

| Data | Where | Could power |
|---|---|---|
| Round end type (`result`) and Riot's round tags (`ceremony`: Ace, Clutch, Flawless, Thrifty, Closer) | `rounds[]` | Round-type breakdowns; a cross-check of our clutch and ace detection |
| Per-round loadout value, credits left, weapon and armor per player | `rounds[].stats[].economy` | Pistol-round win rate, eco and bonus rounds, win rate by team buy |
| Kill order and timing | `kills[]` | Opening duels (first blood / first death), trade speed |
| Kill and plant positions, everyone's position and view angle at each kill | `kills[].location`, `player_locations` | Death maps per map (needs minimap images and coordinate calibration) |
| Assists per kill | `kills[].assistants` | Who sets up whose kills |
| Weapon and weapon type per kill | `kills[].weapon` | Weapon usage; ability and spike kills |
| Damage dealt to each enemy per round | `rounds[].stats[].damage_events` | Damage-per-duel charts |
| Whole-game ability casts | `players[].ability_casts` | Utility usage per agent |
| AFK rounds, friendly fire, rounds in spawn | `players[].behavior` | A (gentle) behavior report |
| Money spent and average loadout | `players[].economy` | Economy discipline per player |
| Party IDs and party RR penalties | `players[].party_id`, `metadata.party_rr_penaltys` | Confirm who queued together |
| Server | `metadata.cluster` | Win rate by server |

Needs new calls (one per member):

| Data | Endpoint | Could power |
|---|---|---|
| RR gained or lost per competitive game | MMR history (v2) | RR per night, rank over time, "who carried the RR" |
| Current rank, peak rank, past acts | MMR (v3) | Rank badges on the Players page, season-over-season progress |
