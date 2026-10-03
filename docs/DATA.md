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
| `members` | squad member (2 to 5) | seeded from config.json once, then the Squad tab (account lookup) | `puuid` (the identity; survives renames), `name`, `tag` (the current Riot ID), `region`, `nickname`, `card`, `order_index`, `added_at`, `previous_name` (the Riot ID before the last rename, `Name#TAG`), `name_checked_ts` (when the current Riot ID was last confirmed), `bettor` (the betting account that owns this entry: signed up with it, added it or claimed it; NULL for entries seeded from config.json or added by the admin until someone claims them), `active` (1 = on the active squad, whose games are tracked; 0 = on the bench: in the pool, not playing right now). `db.members()` is the active squad, `db.pool()` everyone |
| `matches` | squad game (every member on one team) | tracker | `match_id`, `map`, `mode`/`mode_label`, `started_at`/`started_ts`, `season`, `region`, `team`, `rounds_won`, `rounds_lost`, `result` (`win`/`loss`/`draw`), `game_length_ms`, `source` (`stored`/`details`/`roster` (re-derived from `member_games` after a squad change)/`demo`), `party_verified`, `details_fetched` |
| `match_players` | member in a squad game | tracker | `match_id`, `puuid`, `agent`, `score`, `kills`, `deaths`, `assists`, `headshots`, `bodyshots`, `legshots`, `damage_dealt`, `damage_received`, `tier`, `tier_name` |
| `member_games` | member's line in **any** stored Competitive game | tracker | same stats as `match_players`, plus the game's `map`, `mode`, `started_ts`, rounds, `result` and the member's `team` (NULL on rows stored before the column existed). Rows whose `match_id` isn't in `matches` are that member's baseline; after a squad change they are what the squad games are re-derived from |
| `match_timelines` | 5-stack game | tracker (from the v4 record) | `match_id`, `data` (JSON, see below; NULL if the record had no round data), `fetched_ts`. The round charts, recaps and the team "moment" markets (`moments.game_facts`) read it |
| `bettors` | betting account | sign-up, rewards, demo | `name`, `balance`, `created_at`, `salt`, `password_hash` (NULL = unclaimed) |
| `bets` | bet (single or parlay) | bet slip, settlement | `bettor`, `market_id`, `market_type` (`ou`/`exact`/`top`/`team_win`/`team_ou`/`team_ot`/`team_rw`/`team_rl`/`team_margin`/`team_score`/`team_moment`/`parlay`; `team_ou`, `team_rw` and `team_rl` are no longer offered but still settle), `description`, `selection`, `line`, `odds_decimal`, `stake`, `placed_ts`, `context` (JSON: stat, player, direction, `fair_prob` (the model's chance before the house edge, on bets placed since the odds accuracy card), `boost` (the price before the odds boost or a wheel boost token, on a boosted single; `boost_token` is the token's `wheel_perks` id), `insured` (`{perk, max}`: a wheel insurance token, so a loss refunds the stake up to `max`), `custom` (a custom line), or a parlay's `legs`, each with its own `fair_prob`, `boost` (the price before the odds boost of the game, if this leg is its pick) and `hist` (the leg settled at its line on the last 60 games, newest first: `1` won, `0` lost, `-` void), plus `corr` (the price: `odds_decimal`, `independent_decimal`, `factor`, `games`, `linked`)), `status` (`pending`/`won`/`lost`/`void`/`cancelled`), `settled_match_id`, `settled_ts`, `payout`, `actual_value`, `note` |
| `rewards` | member per game | reward manager | `match_id`, `puuid`, `bettor`, `base`, `bonus`, `acs`, `beat_share`, `baseline_games`, `created_ts` |
| `seasons` | ended betting season | season reset | `id`, `name` ('Season N'), `started_ts`, `ended_ts`, `standings` (JSON: the leaderboard at the end), `bets`, `rewards` (counts) |
| `archived_bets` | bet from an ended season | season reset | `season_id` plus every `bets` column; bets still open at the reset are archived as `cancelled` |
| `archived_rewards` | reward from an ended season | season reset | `season_id` plus every `rewards` column |
| `banana_ledger` | change to a bettor's bananas | `bananas.py` (earning after every sync and at start-up, buying), season reset, demo | `bettor`, `delta` (+ earned, − spent), `reason` (`game` (a game the member played, ref `game:<match_id>:<puuid>`), `bet_win` / `reward` (from when bananas were paid per credit gained), `rounding` (the one-time rounding of an old wallet to whole bananas), `starter` (the season's starting bananas, ref `starter:<season number>:<name>`), `purchase`, `prank`, `arcade`, `season_reset`, `demo`, or `test` in the self-test), `ref` (`bet:<id>`, `reward:<match>:<puuid>`, `<item>:<ns>`, `season:<id>:<name>`; unique with `reason`, so nothing is paid twice), `credits` (the credit gain an earning came from), `note`, `created_ts`. A wallet is the sum of its rows |
| `banana_items` | item a bettor owns | Onkey's Shop | `bettor`, `item_id` (see `bananas.CATALOG`), `price`, `bought_ts`; kept through season resets |
| `banana_equipped` | worn slot | Onkey's Shop | `bettor`, `slot` (`name_color`/`badge`/`title`/`banner`/`ticket`/`celebration`/`theme`), `item_id` |
| `banana_pranks` | social item used on someone | Onkey's Shop | `item_id` (see `bananas.SOCIAL`: `sc-peel`, `sc-jinx`, `sc-upside`, `sc-shrink`, `sc-fog`, `sc-clown`, `sc-glitter`, `sc-bounty`, `sc-heckle`, `sc-nick`, `sc-note`, `sc-title`), `bettor` (who sent it), `target`, `text` (a note or title), `price`, `created_ts`, `expires_ts`, `games` (when above 0, also over once that many 5-stack games have started after `created_ts`: 3 for most pranks) |
| `arcade_plays` | paid play in Onkey's Arcade | `arcade.py` | `bettor`, `game` (`catch`/`says`/`dash`), `token` (one-time, unique), `price`, `started_ts`, `finished_ts`, `score` (NULL until the game sends it back; boards use each bettor's best above 0). The price is a `banana_ledger` row with reason `arcade` and ref `play:<token>` |
| `slot_spins` | immediately settled slot spin | `slots.py` | `id`, `bettor`, `machine` (`jackpot`; retired `classic` rows retained), `stake`, `reels` (JSON array of three symbol indexes into `slots.SYMBOLS`, 0-6), `multiplier`, `payout` (stake included), `created_ts`, `request_id` (unique per bettor), `season_id` (NULL for current season; set at reset), `rtp` (the expected return per credit the spin was played at, without anything the secret Golden Onkey pays, e.g. 0.95; NULL for spins from before line stats and house tracking began, which both skip them; added to older databases on open). Balance update and insert are one transaction. Old rows and retry keys survive resets |
| `house_ledger` | the house's side of one casino round | `house.py` (`record()`, inside the round's own transaction) | `id`, `game` (`slots`, `blackjack`, `poker`), `ref` (unique per game: `spin:<slot_spins.id>`, `hand:<blackjack_hands.id>`, `hand:<poker_hands.id>`), `bettor`, `staked` (credits staked against the house; 0 for a game the players bet against each other), `take` (stakes minus payouts for a house-banked game, negative when the bettor wins; a rake otherwise), `expected` (the take the game's edge predicts, NULL when unknown), `created_ts`, `season_id` (NULL for the current season; set at reset). Slot spins from before the table existed are backfilled at start-up. Scaffolding: nothing moves credits from it yet |
| `blackjack_hands` | one bettor's round of blackjack (all their hands: one more for every split) | `blackjack.py` | `id`, `bettor`, `tbl` (`solo` or `shared`), `round`, `stake` (everything staked, doubles and splits included), `payout` (stakes included; the refund for a void round), `hands` (JSON: each hand's `cards`, `stake`, `doubled`, `split`, `done`, `saved` (only on a hand Onkey saved: `index` of the card he changed and `was`, the card that came out of the shoe), `result` (`win`, `lose`, `push`, `blackjack`, `bust`), `payout`), `dealer` (JSON: the dealer's cards), `status` (`playing`, `settled`, `void`), `note` (why a round was voided), `created_ts`, `settled_ts`, `request_id` (unique per bettor), `side` (JSON: side bets, `{kind: {stake, result, payout}}` once settled; NULL without any; their stakes and payouts are in `stake` and `payout`), `tip` (credits tipped to Onkey after the round, to the house; taken off the casino net), `edge` (0.0011; 0.0027 on rounds from when Onkey's save was 1 bust in 200, 0.005 from before unlimited splits and the save), `season_id`. The stake moves with the row's insert, doubles and splits with its stake update, the payout with settling, each in one transaction. Rows still `playing` at start-up or a season reset are refunded and voided. Kept through resets |
| `poker_seats` | the chips at the poker table (the escrow) | `poker.py` | `bettor` (primary key), `seat` (0-7), `stack`, `joined_ts`. Written with each buy-in, top-up and cash-out, and with every finished hand's stacks; a hand in play is never saved, so start-up refunds every row (cancelling an unfinished hand) and empties the table |
| `poker_buyins` | money in and out of the poker table | `poker.py` | `id`, `bettor`, `kind` (`buyin`, `topup`, `cashout`), `amount`, `created_ts`, `season_id`. Kept through resets |
| `poker_hands` | a finished poker hand | `poker.py` | `id`, `started_ts`, `ended_ts`, `settings` (JSON: the rules it was played under), `board` (JSON cards), `players` (JSON: each `seat`, `bettor`, `start`, `end`, `net`, `won`, and at a showdown `cards`, `hand` (e.g. "Pair of Kings") and `best` (the five cards)), `pots` (JSON: each pot's `amount` after the rake, `winners`, `hand`), `pot` (after uncalled bets went back), `rake`, `season_id`. Kept through resets |
| `poker_results` | one player's net from a poker hand | `poker.py` | `id`, `hand_id`, `bettor`, `start`, `end`, `net` (chips won minus chips put in; the hand's nets sum to minus its rake), `season_id`. The Casino column's poker part. Kept through resets |
| `transfers` | credits one bettor sent another | Send credits (`BetManager.send`), settlement (the tax) | `id`, `sender`, `recipient`, `amount`, `note` (NULL if none), `created_ts`; the generosity tax: `tax_status` (`open` = waiting on the recipient's next winning bet, `paid`, NULL = this transfer earned none), `tax_amount`, `tax_bet_id` (the bet it came from), `tax_match_id`, `tax_ts` |
| `archived_transfers` | transfer from an ended season | season reset | `season_id` plus every `transfers` column |
| `loans` | credits a bettor borrowed from Onkey's Bank | `bank.py` (`db.take_loan` / `db.repay_loan`) | `id`, `bettor`, `principal`, `interest`, `owed` (principal + interest), `repaid`, `taken_ts`, `cleared_ts` (NULL while open; open principal counts against `loan_max`) |
| `archived_loans` | loan from an ended season | season reset | `season_id` plus every `loans` column |
| `hunt_days` | a bettor's Banana Hunt picks on one Pacific day | `hunt.py` (`db.hunt_pay`) | `id`, `bettor`, `day` (`wheel.wheel_day()`), `bananas`, `credits`, `updated_ts`, `season_id` (NULL = the current season; a reset tags the rows, so a day that straddles one gets a second row). `credits` is what the daily cap counts (a golden banana, a catch or a combo pays more than one a pick). The hidden item of the day is a `banana_ledger` row with reason `hunt` and ref `hunt:<bettor>:<day>` (25 bananas, or 0 with a `wheel_perks` token beside it), so it's handed over once |
| `house_objectives` | secret objective | `house.py` | `id`, `scope` (`player` / `squad` / `bettor`), `kind` (a stat for personal goals: `kills` / `assists` / `acs` / `adr` / `hs_pct` / `deaths`; `win` / `margin` / `pistol` / `flawless` / `ace` / `comeback` for squad goals; `underdog` / `small` / `on_player` / `unlucky` / `show_up` for bettor goals), `target` (a member's puuid, for personal goals and `on_player`), `params` (JSON, e.g. `{"n": 21}`), `text`, `chance` (estimated from earlier games; sizes the prize), `prize`, `drawn_ts` (the game after `after_match`'s start + 1: the set applies to the first complete game that started after it), `after_match`, `status` (`open` until revealed, then `met` / `missed` / `void`), `match_id` (the game it was revealed on), `settled_ts`, `winners` (JSON {bettor: credits paid}), `created_ts`. One open set at a time; never archived |
| `house_payouts` | credit the house gave back | `house.py` | `id`, `bettor`, `amount`, `kind` (`objective` / `refund` / `insurance` / `wheel` / `jackpot`), `ref` (unique: `objective:<id>:<bettor>`, `refund:<bet id>`, `insurance:<bet id>` or `wheel:<spin id>`, so nothing is paid twice), `match_id`, `note` (the objective's text, or `Bad beat: …`), `created_ts`, `season_id` (NULL for current season; set at reset). Insert and balance update are one transaction |
| `wheel_spins` | daily wheel spin | `wheel.py` | `id`, `bettor`, `day` (the Pacific date it counts for, `wheel.wheel_day()`), `segment` (index into `wheel.SEGMENTS`, the slice it stopped on), `prize` (the slice's key: `c100` / `c250` / `c500` / `c1000` (credits; spins from before the prizes grew have `c10` / `c25` / `c50` / `c100` / `c200` / `c400`, their label and amount saying what was paid), `b50` / `b100` (bananas; older spins `b20` / `b50`), `boost`, `insure`, `item`, `again2` (2x respin), `jackpot`; older spins also `again` (Spin again) and `ate` (nothing)), `label` (what was won, e.g. `Free cosmetic: Gold name`, `Jackpot: 102 credits`), `amount` (credits or bananas, when an amount), `detail` (JSON: a cosmetic's `item_id` and `slot`), `created_ts`. Kept through resets |
| `wheel_perks` | daily wheel token | `wheel.py`, `bets.place()` | `id`, `bettor`, `kind` (`boost` / `insurance`), `status` (`ready`, then `used`), `spin_id`, `bet_id` (the single that used it), `created_ts`, `used_ts` |
| `meta` | setting | various | JSON values: `cookie_secret`, `rejected_matches`, `last_sync`, `backfilled`, `history_backfilled`, `rewards_since`, `bananas_since` (games that started before it never pay bananas; set the first time bananas are paid), `season_started`, `roster_seeded` (the squad was seeded from config.json; from then on the file's `members` are ignored), `odds_boost` (the odds boost of the game: `after` (the latest game when it was drawn; a new game draws again), `market_id`, `selection`, `drawn_ts`) |

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
| `GET /api/status` | Config state, squad members (with their bettor account, `linked`, `previous_name`, `added_at`, `name_checked_ts`), `roster` (`min`, `max`, `size`, `editable`, `admin_required`), record, tracker and sync state, rate limit, reward settings, generosity tax settings (`tax_rate`, `tax_min_transfer`), `banana_per_game`, bank terms (`loan_max`, `loan_interest`), `hunt_daily_max`, `hunt_floor`, `modes` (always `["competitive"]`), tunnel, log |
| `GET /api/roster` | The pool: `members` (the active squad, as in `/api/status`), `bench` (the rest, same shape) and `roster` (`min`, `max`, `size` = active, `pool` = everyone, `editable`, `admin_required`) |
| `POST /api/roster` | Add a player to the pool by `riot_id` (looked up on HenrikDev), optional `nickname`; on the bench unless `active: true` (refused when the squad is full). A Riot ID of a known player just updates them (`added: false`, `renamed`). Returns `member`, `members`, `bench`, `changes` (`removed` / `added` games from the re-derivation, only when the active squad changed; a full re-scan then starts). Needs the admin password header when one is set; refused in demo mode |
| `POST /api/roster/active` | Set the whole line-up: `puuids` (2 to 5 pool players, in order) become the active squad and everyone else the bench. `changed` says whether the set of players changed (an order-only change re-derives nothing); with it come `changes` and a full re-scan (admin as above) |
| `POST /api/roster/{puuid}` | Set a member's `nickname` (admin as above) |
| `DELETE /api/roster/{puuid}` | Remove a player from the pool. From the active squad (never below 2) that re-derives the games and starts a full re-scan; from the bench it changes nothing else (`changes.was_active`) (admin as above) |
| `POST /api/roster/refresh` | Look up every member's current Riot ID now; returns `renamed` (`{puuid, from, to}`) |
| `GET /api/stats` | Players page data: per member overall, per agent, per map, form (last 15 games), best games, `range` (highest and lowest complete game for ACS, K/D, kills, deaths, assists, ADR and HS%), 5-stack vs other games; and `timeline`: every complete game oldest first (`games`) with each member's per-game values of those stats lined up with it (`series[puuid][stat]`), for the trend chart |
| `GET /api/forecasts?stat=<key>&player=<puuid>&map=&agent=` | Forecasts tab data for one stat (`kills`, `deaths`, `assists`, `kpr`, `dpr`, `apr`, `acs`, `adr`, `hs_pct`; default `acs`; 400 for anything else): every player's `forecast_games` count, and for one player (default the first with forecasts) each forecast game (`range` = [low, high], `typical` = median, `expected` = mean, `actual`), `next` (the forecast for their next game from all their games so far, on `map` as `agent` when given, the odds board's context: `range`, `typical`, `expected`, `map`, `agent`, `games` used; null before 5 games), the `overall` record and the map × role `cells` (averages of those, plus above / inside / below counts and agents) with `best` / `worst` |
| `GET /api/insights` | Visualizations tab data: games (each with its `damage_share` and `damage_cum`, the running all-time share), moments, sessions, maps, players (maps, aim, agents), comps, round insights (clutches, multi-kills, spike sites) |
| `GET /api/betting-report` | The Standings page's betting cards: ROI by market type per bettor, bets on yourself vs others, each bettor's profit over time (`bankroll`), and the odds accuracy card (`accuracy`: picks, won, expected wins and a verdict overall, per market type and per chance bin) |
| `GET /api/content` | Known maps and agents, and which agents each member plays (the page no longer uses it: the Place bets map and agent pickers were removed) |
| `GET /api/matches?limit=` | 5-stack games newest first, each with its players and `ending` (`complete`/`forfeit`) |
| `GET /api/match/{id}` | One game with its players |
| `GET /api/recap?match=<id>` | Matches tab recap for one game (the latest if `match` is missing or unknown; `null` with no games): `match` header (with `night_game`, `night_record`, `number`, `older` / `newer` ids), `players` (this game's line, `usual` from earlier complete games, `forecast` for kills / deaths / ACS, `rounds` totals), `rounds` (per round: `won`, running `score`, `side`, spike, `first_blood`, `multi`, `clutch`), `betting`, `rewards`, ranked `highlights` ({`score`, `kind`, `title`, `detail`, `puuid`, `tone`}) and `house` (the game's revealed `objectives`, as in `/api/house` `last`, and its bad-beat `refunds`: `bettor`, `amount`, `note`) |
| `GET /api/odds?map=&agents=` | The odds board: team markets (`team:win`, whose `basis.recent` is the last five results newest first; `team:ot`; `team:margin` and `team:score` covering every result, losses and overtime included; and, once 5 games have a timeline, the round "moment" markets `team:pistol` / `team:half` / `team:ace` / `team:comeback` (yes / no) and `team:flawless` (over / under), `type: "team_moment"` with the fact in `fact`; no total-rounds or rounds won / lost markets any more), player props (over / under on kills, deaths, assists and HS% only, `odds.PROP_KEYS`; `stat_defs` lists those four), top/bottom-of-scoreboard markets. The page never sends `map` or `agents` now. A selection that would have won the last 3+ games in a row at today's line carries `streak` (the run length, counted back through `streak_lookback` games; voids skipped), shown as a flame border; a roughly 50/50 pick (fair chance 35–65%) in a market with three or more picks that lost as many carries `cold` instead, shown frosted (two-way markets never do: the other side already has the flame). `boost` is the odds boost of the game (`market_id`, `selection`, `description`, `from_decimal` / `from_american`, the boosted `decimal` / `american`, `pct`, `max_stake`, `fair_prob`), and its selection on the board carries the same `boost` with its `decimal` / `american` already raised |
| `GET /api/odds/custom?puuid=&stat=&line=&map=&agents=` (or `&exact=N` instead of `line`) | A custom line's `market` (id `alt:<stat>:<puuid>:<line>`; an exact number's is `exact:<stat>:<puuid>:<N>`, type `exact`, one `exact` selection needing a 2% chance, `limits.exactly`): the reasonable `limits` for "at least" / "at most", `typical`, and over / under `selections`, each with `available` (fair chance between 5% and 90%) or a `reason`; 400 for a bad line, player or stat |
| `GET /api/bets?status=&bettor=&limit=` | Bets newest first, with the game each settled on (`game_map`, `game_rounds_won`, ...) |
| `GET /api/bettors` | Leaderboard, ranked by balance minus `debt`: balance, `debt` and `borrowed` (what they still owe Onkey's Bank, interest included, and the open principal; loans are left out of profit), `hunt` (credits picked in the Banana Hunt this season, left out of profit too), match-betting-only profit, rewards, `transfers` (net credits received from other bettors), `casino` (current season payouts minus stakes across every casino game, `house.casino_nets()`: slots, blackjack and poker, excluded from profit and ROI), `slots` (the slots part of it), `giveaways` (this season's credits from the house: objectives, bad-beat refunds, insurance and daily wheel prizes, excluded from profit and ROI), record, ROI, open stakes |
| `GET /api/rewards?limit=` | Recent game rewards |
| `GET /api/transfers?bettor=&limit=` | This season's transfers newest first (default 50), optionally only those a bettor sent or received; a paid tax also carries `tax_bet_description` and `tax_bet_net` (the taxed bet's net winnings) |
| `POST /api/transfers` | Send credits as the signed-in bettor: `to`, `amount` (at least 1, at most the balance, rounded to cents), optional `note` (80 characters); returns the `transfer`, the sender's new `bettor` and the leaderboard |
| `GET /api/bank` | Onkey's Bank: `bank` (the terms: `max`, `interest`, `min`, `enabled`) and `me` for a signed-in bettor (`borrowed`, `debt`, `room` (what they can still borrow), `open` loans each with `due`, recent `cleared` loans), else `null` |
| `POST /api/bank/borrow` | Borrow `amount` whole credits as the signed-in bettor, within `room`; returns the `loan`, `me`, `bank`, the `bettor` and the leaderboard (400 with the reason otherwise) |
| `POST /api/bank/repay` | Pay back `amount` whole credits (everything owed if omitted or more than the debt), oldest loan first, out of the balance; returns `repaid` (`paid`, `cleared` loan ids), `me`, `bank`, `bettor`, `bettors` |
| `GET /api/hunt` | The Banana Hunt: `hunt` (the terms: `enabled`, `daily_max`, `floor` (under this many credits the cap doesn't apply), `per_banana`, `field` (`w`, `h`, the hit radius `r`, in CSS pixels), `min_interval_s`), `board` (top pickers: `name`, `today`, `season`, `all_time`, `bananas`) and `me` for a signed-in bettor (`today`, `left` (what the cap leaves, or, past it and under the floor, what gets them back to the floor), `done`, `under_floor`, `balance`, `floor`, `day`, `resets_ts`, `season`, `all_time`, `bananas`, and the banana to draw: `target` `{x, y}` in field pixels, or `null`), else `null`. `hunt` also carries `extras`, `theme` (the field of the day: `jungle` / `night` / `rain` / `beach` / `ruins`), `air`, `gold`, `bunch`, `combo`, `freeze_s`, `greg_s` and `streak_max` (the extras' numbers, for the page); `me` also carries `picks` (bananas today), `cap` (the day's cap with the streak's bonus), `streak` (`days`, `bonus`, `paid`), `combo` and `mult` |
| `POST /api/hunt/start` | Make sure the signed-in bettor has a target (one already down keeps its spot and kind and restarts its timers; it's never swapped); returns `me` |
| `POST /api/hunt/click` | Report a click at `x`, `y` (field pixels), with `air` (0-1, how far along its arc the banana was, for a catch in the air) or `shoo: true` (the click was on Greg): `hit`, `paid`, `today` (credits picked today), `left`, `done`, `under_floor`, `combo`, `mult`, `target` (the next thing to draw: `id`, `kind` (`banana` / `golden` / `bunch`), `x`, `y`, and by kind `items` (a bunch: `x`, `y`, `picked`), `decoy` (the rotten banana's spot), `greg` (where he walks in from), `claw` (true: the scientist's claw is coming for it) or `strudel` (where Man Strudel walks in from; he takes nothing); null once done), `balance`, and on a hit `kind`, `air`, `swept` / `bunch_bonus`, `streak_bonus` / `streak`, `found` (the hidden item: `kind`, `amount`, `label`, or null). When not a hit, `reason`: `miss`, `too_fast`, `done`, `frozen` (after a rotten banana, with `frozen_s`), `rotten`, `shooed`, or what became of a target left too long (`rotted`, `bunch_over`, `stolen`, `clawed`). 403 signed out |
| `POST /api/hunt/next` | The page's timer on a golden banana, a bunch or Greg ran out: replaces the target if the server agrees it lapsed and returns `reason` (`rotted` / `bunch_over` / `stolen` / `clawed`, or null while it's still good), `today`, `left`, `done`, `under_floor`, `combo`, `mult`, `target`. 403 signed out |
| `POST /api/scientist/refuse` | Refuse the scientist's offer for Onkey (see `docs/onkey-lore.md`): gives the signed-in bettor the `tt-notforsale` title for free, once (a `banana_items` row at price 0); returns `item` (`id`, `name`). 403 signed out |
| `GET /api/bettor/me` | The signed-in bettor, if any: `name`, `balance` and `bananas` (the top bar's credits and bananas chips, and the bet slip), `open_bets` and `open_stake` (the bet slip and the Overview), `member` (their pool entry: `puuid`, `name`, `tag`, `nickname`, `active`; `null` if they're not in the pool), and `recent_wins` (their last 20 won bets, newest settled first: `id`, `description`, `market_type`, `stake`, `odds_decimal`, `payout`, `settled_ts`), which the page celebrates with confetti, `recent_received` (the last 20 transfers other bettors sent them, newest first), which it announces once, `recent_taxes` (the last 20 generosity taxes they collected, newest paid first, with the bet's `bet_description`), which it also announces once, `recent_giveaways` (the last 20 `house_payouts` rows paid to them, newest first), announced the same way, `wheel_ready` (true while today's daily wheel spin is waiting: the top bar's Spin ready chip and the Casino menu's dot), `tokens` (`{boost, insurance}`: daily wheel tokens ready, for the bet slip's toggles), and `recent_settled` (their last 12 settled bets, newest first: `id`, `status`, `description`, `stake`, `payout`, `odds_decimal`, `settled_ts`; Onkey reacts to new ones and to losing runs). `scientist_offer` is true while the scientist's offer is open to them (fewer than 100 credits, and not refused yet) |
| `POST /api/bettor/register`, `/login`, `/logout`, `/password` | Bettor accounts. `register` also takes an optional `riot_id`: the new account's owner joins the squad with it (`member` in the reply), or `warning` says why not (squad full, unknown Riot ID, demo mode); the account is created either way |
| `POST /api/bettor/riot-id` | Signed-in bettor: put your `riot_id` in the pool (optional `nickname`): onto the squad when there's a spot, else onto the bench (`active`). Adds you, updates your entry if you renamed, claims an unowned entry with that Riot ID, or switches you to another account (your old entry leaves and the new one takes its place, squad or bench). Refused if the Riot ID belongs to another bettor's entry. Returns `member`, `added` / `renamed` / `replaced` / `active`, `changes`; starts a full re-scan when the games that count may have changed |
| `POST /api/bettor/riot-id/active` | Signed-in bettor: `active: true` to swap yourself onto the squad (refused when it's full), `false` to sit out on the bench (never below 2 on the squad); returns `changed`, `changes`, `member` |
| `DELETE /api/bettor/riot-id` | Signed-in bettor: remove your Riot ID from the pool (from the active squad never below 2 players; `changes.was_active`) |
| `POST /api/bettor/clear-password` | Admin: free a bettor name |
| `POST /api/odds/parlay` | Price a parlay without placing it (`legs`, `context`): `odds_decimal` (what it would pay), `independent_decimal` (the legs' odds multiplied), `factor` (the cut for linked legs, 1 = none), `games` (games replayed), `linked` (groups of leg indexes that won together more than chance) and `legs` (each with `market_id`, `selection`, `description`, `odds_decimal`, `boost`: the pre-boost decimal, if this leg is the odds boost of the game's pick or carries a boost token, and `boost_token`: whether it's a token; a leg sent with `"boost": true` is priced with a boost token); 400 with the reason when the legs can't share a parlay |
| `POST /api/bets` | Place a single (`market_id`, `selection`, `stake`, optional `tokens`: `{"boost": true, "insurance": true}` to use daily wheel tokens on it; 400 if one is asked for but not held, or a boost on a pick over 250 credits or already boosted) or a parlay (`legs`, `stake`; a leg may carry `"boost": true` to use a boost token on it, one token per such leg, the parlay capped at 250 credits; the built leg stores the token's id as `boost_token` and its price before the boost as `boost`) |
| `DELETE /api/bets/{id}` | Cancel a pending bet |
| `GET /api/seasons` | The current season (start, bet, reward and transfer counts) and every past season with its final standings |
| `POST /api/bettors/reset` | End the season: needs `{"confirm": "RESET"}` (and the admin password header if one is set); archives, then resets |
| `GET /api/shop` | Onkey's Shop: `per_game` (bananas per Competitive game played), `groups` (`key`, `label`, `desc`: `looks`, then `casino`, Onkey's Casino), `slots` (each `key`, `label`, `desc`, `group`), `catalog` (each item's `id`, `slot`, `name`, `price`, `desc` and `look`: what the page draws, e.g. `cls`, `emoji`, `text`, `colors`, `theme`), `social` (items used on someone else, with `hours`, `games`, `max_len`), and `me` for a signed-in bettor: `wallet`, `earned`, `season_earned`, `season_games` (games paid this season) (the credits those came from), `spent`, `owned`, `worn` ({slot: item id}) and `history` (their last 25 ledger rows) |
| `POST /api/shop/buy` | Buy an item (`{"item": id}`, worn straight away) or use a social one (`{"item", "target", "text"}`); returns `item`, `wallet` and the updated `shop`. Needs the bettor session |
| `POST /api/shop/equip` | Wear an owned item (`{"slot", "item"}`) or take a slot off (`"item": ""`); returns `worn` and `shop` |
| `GET /api/troop` | The Monkeys tab: every bettor with `wallet`, `earned`, `season_earned`, `season_games` (games paid this season), `spent`, `items`, `collection` (bananas' worth of items owned), `credits` and betting `profit`, most collected first; plus `looks` ({lower-cased name: {`worn`: {slot: look}, `pranks`: [active pranks on them, with `games_left`]}}), which the page uses to style names everywhere |
| `GET /api/troop/profile?name=` | One bettor's profile on the Monkeys tab: the same fields, `owned` items, `worn`, `pranks` received (each with `active`), `pranks_sent`, `betting` (balance, profit, rewards, record, ROI) and `looks`; 404 for an unknown name |
| `GET /api/arcade` | Onkey's Arcade: `price`, `games` (each `key`, `name`, `icon`, `desc`, `plays`, `board`: the top 10 bettors' best scores, and the signed-in bettor's `my_best`) and `me` (`wallet`, `plays`) |
| `GET /api/slots` | `machines` (one entry, key `jackpot`, name `Slots`: `tickets` (1,000,000) and `lines` per symbol index (each triple's tickets: banana 4,000, cherry 50,000, bell 40,000, diamond 8,000, spike 20,000, Onkey 2,000, Golden Onkey 50), `triples` by symbol index (banana 40, cherry 3, bell 4, diamond 20, spike 8, Onkey 80, Golden Onkey 100), the Golden Onkey's other outcomes: `spotted` (2: a lone one with no line pays 2× the stake), `spot1` (3,000 tickets for that), `wild_factors` (`[1, 2, 3]`: filling in a line, one Golden Onkey doubles it and two triple it) and `golden_cap` (100: no win with a Golden Onkey pays more), `wild1` / `wild2` per symbol index (a pair it finishes, ×2: 20, 250, 200, 40, 100, 10; a symbol with two of them, ×3: 2 each), `chances` (each natural triple's probability, `lines` / `tickets`, rarer the bigger the payout: cherry 1 in 20 up to Onkey 1 in 500 and the Golden Onkey 1 in 20,000), `win_chance` (the chance a spin pays anything, 0.127682), `show` (the display weights a losing spin's reels are drawn from, cherries most; losses never show the Golden Onkey; the page builds its reel strips from them, leaving out secret symbols), `rtp: 95.0` (percent, the house edge's side: the regular lines without anything the Golden Onkey pays), `rtp_with_secret: 97.05`), `symbols` (`key`, `icon`, `name`, optional `img` drawn instead of the emoji, `glow` (the Golden Onkey: Onkey's picture with a golden glow), and `secret` on the Golden Onkey, which the page leaves out of the pay table and the reel strips), `stakes` (5, 10, 25, 50, 100, 250, 500), `house` (every bettor, every season, spins with `rtp` only: `since`, `spins`, `staked`, `paid`, `secret_paid` (what spins showing a Golden Onkey paid, which the edge doesn't cover), `expected_take` = stakes × (1 − rtp), `actual_take` = staked − paid), `big_wins` (this season's five biggest wins by anyone, a win being a payout above the stake: `bettor`, `stake`, `payout`, `multiplier`, `created_ts`, `reels`, `net`, `parts`), and signed-in `me` (`name`, season `spins`, `staked`, `returned`, `net`, `wins`, `best` (the biggest win this season, a parsed spin, or null) and `since_win` (spins since the last win this season)), `history` (last ten current-season spins, parsed reels, net and `parts`) and `lines` (every season, spins with `rtp` only: `spins`, `since`, `hits` per symbol index, a line the Golden Onkey finished counting for its symbol). Every parsed spin carries `parts`, what it paid piece by piece (`slots.payouts()`): `{"kind": "line", "symbol", "mult", "wild"}` (`wild`: Golden Onkeys that filled it in), then `{"kind": "wild", "count", "factor", "mult"}` (the multiplying: `mult` is what it adds, the line × (factor − 1) cut so the spin pays at most `golden_cap`, with `capped` true when it was) or, with no line, `{"kind": "spotted", "count", "mult"}`; a spin from before the Golden Onkey paid on its own keeps what it recorded (one line, or none). Signed out: `me: null`, `lines: null`, empty history |
| `GET /api/house` | The house (`house.py`, `HouseManager.report()`): the casino ledger's take, `games` (each `game` with `season` and `all_time` totals: `rounds`, `staked`, `take`, `expected`), `season_take` and `all_time_take`; then, every season (archived rows included): `bets` (settled won / lost bets: `since`, `bets`, `staked`, `paid`, `expected_take` = each stake × (1 − price × the model's chance before the edge, every leg that stood multiplied for a parlay, a boosted bet at its price before the boost; `odds.fair_chance()` for bets placed before `fair_prob` was saved), `actual_take` = staked − paid; voids and cancels are refunds and count for neither), `casino` (from `house_ledger`: `rounds`, `staked`, `actual_take`, and `expected_take`, each round's `expected`, poker's rake as its take, nothing for slot spins from before they recorded their return), `rewards_paid` (every game reward paid out), the totals `expected_take` / `actual_take` of bets and the casino, `pot` (50% of the expected take minus every objective and bad-beat refund paid; the daily wheel's credits and insurance refunds are free and don't count) and `jackpot` (the other 50% minus jackpots paid), `given` ({kind: credits paid}), `next` (the open objectives: `objectives` (how many) and `prizes`, never their text), `last` (the most recently revealed game's objectives: `scope`, `text`, `prize`, `chance`, `status`, `winners`), `recent` (the last 8 `house_payouts` rows) and `rules` (`jackpot_share`, `game_share`, `refund_rate`, `refund_max`). Drawing the next set happens on start-up and after each game, not on this request |
| `GET /api/wheel` | The daily wheel: `segments` (`key`, `label`, `kind` (`credits` / `bananas` / `boost` / `insurance` / `again` / `item` / `jackpot`), `amount`, `weight`), `total_weight` (1000; a slice's chance is weight / total, and its share of the wheel), `jackpot` (what the jackpot slice pays now), `day`, `next_reset` (next midnight Pacific, epoch seconds), `token` (`boost`, `max_stake`), `unlimited` (true in demo mode: no daily limit), `recent` (the last 12 spins by anyone: `id`, `bettor`, `prize`, `label`, `amount`, `detail`, `created_ts`, `segment`), `biggest` (jackpots won), and signed-in `me` (`name`, `spins_left` (1 or 0), `perks` (ready tokens: `id`, `kind`, `created_ts`), `history` (their last 10 spins)) |
| `POST /api/wheel/spin` | Needs a bettor session. Body `{}`, or in demo mode `{"segment": "<slice key>"}` to ask for a slice (ignored on a live server). One spin per Pacific day (two more after a "2x respin"; no limit in demo mode); the server draws the slice (`secrets`), pays it and returns the spin row plus `balance` and `spins_left`. 403 signed out, 400 once today's spin is used |
| `POST /api/slots/spin` | Needs a bettor session. Body: numeric `stake` from the allowed list, `request_id` (16–80 ASCII letters/digits/hyphens/underscores), optional `machine` (defaults to `jackpot`). Server picks the outcome first (`slots.outcomes()`: each triple at its `chances` probability, then the Golden Onkey's wild lines and lone sighting on random reels, else a non-matching combination drawn from `show` without the Golden Onkey), atomically pays the result and records the machine's `rtp`. Returns `spin` and current `balance`. Reusing the reference returns that spin without charging, including saved retired-machine results; new retired-machine spins are rejected. A different machine or stake with the same reference is rejected. 403 signed out, 400 invalid inputs or insufficient credits |
| `GET /api/blackjack` | `?table=solo` (default) or `shared`; with `since=<version>` on the shared table it long-polls (answers when the table's version passes it, or after 20 seconds). The table as the viewer sees it: `table`, `phase` (`betting`, `playing`, `done`), `round`, `version`, `seats` (each `bettor`, `seated`, `stake`, `playing`, `side` (the side bets placed: `{kind: stake}`), `side_results` (after the deal, per side bet: `stake`, `result` (`perfect`, `coloured`, `mixed`; `suited_trips`, `straight_flush`, `trips`, `straight`, `flush`; or null), `name`, `pays`, `payout`), `streak` (wins in a row, or losses as a negative number, while the server runs), `hands` with `cards`, `total`, `soft`, `blackjack`, `stake`, `doubled`, `split`, `done`, `result`, `payout`, `turn`, and `saved` on a hand Onkey saved), `max_seats`, `dealer` (`cards`, the hole card `null` while playing; `total` of what shows; `blackjack`), `turn` (`bettor`, `hand`), `deadline` (betting window or turn, server time), `next_round_at`, `server_time`, `shoe_left`, `log` (the last 40 events: `id`, `ts`, `kind`, `bettor`, `amount`, ...), `stakes`, `rules`, `edge`, `side_bets` (`open`: whether side bets are taken (off for now); `pairs` / `plus3`: `pays` per result and `edge`; `names`), `emotes`, `turn_s`, `bet_window_s`, `shared_seats` (`taken`, `max`, `names`), `looks` (the shop looks of everyone at the table, the log's players and the viewer, keyed by lower-cased name, as in `/api/troop`; fresh with every answer), and signed-in `me` (`name`, `seated`, `bet`, `step` (send it with your next move), `actions` (what you can do now: `hit`, `stand`, `double`, `split`; a split ace that drew another ace only `stand` and `split`), `hint` (on your turn, else null: `move`, the legal move worth most on average, and `ev`, each legal move's expected value per credit of the hand's stake, from `bjstrategy.best()`), `peek` (on your turn, now and then: what Onkey claims about a card he shouldn't have looked at, `kind` (`next`, the top of the shoe, or `hole`, his hole card), `card` (a rank, which may be a lie), `step`; the truth never leaves the server until the card shows), `peek_result` (once a claimed card has shown, the latest: `kind`, `claimed`, `actual`, `honest`, `step`), `tip` (once you've bet this round: `open` (a won round not tipped yet), `max` (what you won), `tipped`, `amounts`), `turn`, `balance`, `season` (`hands`, `staked`, `returned`, `wins`, `blackjacks`, `net`)) |
| `POST /api/blackjack/sit`, `/leave` | Shared table only; needs a bettor session. Sit down (5 seats), or leave between rounds (a bet not dealt yet is refunded). Returns the table |
| `POST /api/blackjack/bet` | Body: `table` (`solo` or `shared`), numeric `stake` (5, 10, 25, 50, 100, 250 or 500), optional `side` (`{pairs, plus3}`: side bets, each 0 or one of the stakes no bigger than `stake`, taken with it), `request_id` (16-80 letters, digits, `-` or `_`; a retry with the same one isn't charged again). A solo bet deals at once; a shared one waits for the betting window. Returns the table |
| `POST /api/blackjack/emote` | Body: `table`, `emote` (one of the table's `emotes`: 👏 😂 😬 🔥 🙈 🍌). At the shared table you must be seated; one every 1.5 seconds. Logged as an `emote` entry, which the page pops over your seat (a banana is thrown at Onkey). Returns the table |
| `POST /api/blackjack/tip` | Body: `table`, `amount` (5, 10 or 25). After a round you won, before the next one opens: up to what you won (`me.tip.max`), once a round. Takes it off the balance, saves it as the round's `tip`, adds it to that round's `house_ledger` take and expected, and logs a `tip` entry (Onkey thanks you). Returns the table |
| `POST /api/blackjack/action` | Body: `table`, `action` (`hit`, `stand`, `double`, `split`), `step` (from `me.step`; a stale one is refused). Returns the table |
| `GET /api/poker` | With `since=<version>` it long-polls (as above). The table as the viewer sees it: `phase` (`lobby` or `playing`), `version`, `server_time`, `settings` (`limit` (`no`, `pot`, `fixed`), `small_blind`, `big_blind`, `min_buyin`, `max_buyin`, `turn_seconds`, `rebuys`), `seats` (8 entries, `null` when open, else `seat`, `bettor`, `stack`, `ready`, `leaving`, `timeouts`, `dealt`, `folded`, `allin`, `bet` (this street), `label` (their last action), `button`, `sb`, `bb`, `cards` (only your own, or everyone's still in at a showdown), `hidden` (face-down cards to draw), `to_act`), `hand` (`no`, `street` (`preflop`, `flop`, `turn`, `river`, `done`), `board`, `pot`, `current`, `to_act`, `deadline`, `step`, `limit`, or null), `last` (the last finished hand: `no`, `id`, `board`, `pots`, `rake`, `pot`, `showdown`, `players`, `ended_ts`), `next_hand_at`, `log` (the last 40 events), `max_seats`, `choices` (`limits`, `blinds`, `turns`, `buyin_min`, `buyin_max`, `min_buyin_blinds`), `rake` (`rate`, `cap`), `looks` (as for blackjack), and signed-in `me` (`name`, `balance`, `seat`, `season` (`hands`, `net`, `won`, `best`), and when seated `stack`, `ready`, `in_hand`, `topup_room`, `legal` (on your turn: `fold`, `check`, `call` (amount), `raise` (`min` / `max` raise-to totals, or null), `verb` (`Bet` or `Raise`))) |
| `POST /api/poker/sit`, `/leave`, `/ready`, `/topup`, `/settings` | Need a bettor session. `sit` body `buyin` (whole credits within the table's range and your balance); `leave` (folds you if you're in a hand and cashes out when it ends); `ready` body `ready` (true / false); `topup` body `amount` (between hands, up to the maximum buy-in, if top-ups are on); `settings` body `settings` (any of the keys above; seated players in the lobby only; un-readies everyone). Each returns the table |
| `POST /api/poker/action` | Body: `action` (`fold`, `check`, `call`, `raise`, `allin`), `amount` (the raise-to total, for `raise`), `hand` and `step` (from `hand`; a stale pair is refused). Returns the table |
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
