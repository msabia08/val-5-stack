# 5-Stack Tracker

A small local website that follows your Valorant 5-stack through the
[HenrikDev API](https://docs.henrikdev.xyz). It only counts games where **all
five of you were on the same team**, keeps per-game averages overall, per agent
and per map for each player, and turns that history into **betting lines for
your next game** that the squad can bet on with virtual credits.

No Node, no build step, no third-party packages: Python 3.10+ and a browser.

## Quick start

1. **Get an API key** (free). Open <https://api.henrikdev.xyz/dashboard/>, sign in
   with Discord, choose *API Keys* and generate a **Basic** key.
2. **Configure.** Copy `config.example.json` to `config.json` (the server does
   this for you on first run) and fill in:
   - `api_key`
   - `region`: `na`, `eu`, `ap`, `kr`, `latam` or `br`
   - `members`: the five Riot IDs as `Name#TAG`, with an optional `nickname`
     (and an optional `bettor` account name for game rewards)
3. **Run it.**
   ```
   python server.py
   ```
   or double-click `run.bat`. The site opens at <http://localhost:8080>.

The first sync pulls each member's stored match history and keeps only the
games where all five of you were together. After that it re-checks every
`poll_interval_minutes` (default 10), records new 5-stack games and settles any
open bets.

Want to see it with data before you have a key? Run `python server.py --demo`.
That uses a separate synthetic database (`data/demo.db`) and never touches the API.

## What it tracks

- **Team:** record, win rate, streak, average round differential, per-map and
  per-mode records, recent results.
- **Each player:** games, win rate, ACS (combat score per round), K/D, KDA,
  kills / deaths / assists per game, ADR, headshot %, kills per round, plus the
  same breakdown **per agent** and **per map**, recent form and best games.
- **5-stack vs. their other games:** how each player's 5-stack numbers differ
  from their games outside the stack (see below).
- **Each match:** map, mode, score, date, each member's line, and (when the full
  match record is fetched) game length, ranks and whether all five shared a party.
  The **Matches** tab opens with a recap of the latest game (see below); click
  any game in the list, or use *Older* / *Newer*, to recap another.
- **Overview:** the front page. Squad totals, the last game with its top five
  highlights (linking to the full recap), your balance and the top bettors, the
  standout bets of the last 5 games (biggest win and loss, longest odds won,
  shortest odds lost), who's trending (each player's ACS and K/D over their last 5 games
  against the 10 before, with an ACS sparkline), and win rate by map.
- **Charts tab:** charts built from all of the above, grouped by the buttons at
  the top (All, Results, Players, Rounds). Each has a one-line takeaway and hover
  details:
  - record in close games vs. blowouts, win rate after a win vs. after a loss,
    and first game of the night vs. later games
  - form over time (rolling 10-game win rate plus every game's round margin)
  - when you win: day of week × time of day, in your local time
  - whether the squad fades later in a night
  - each player's ACS on each map against their own average
  - who swings results (ACS in wins vs. losses)
  - aim profile: a figure per player, head / body / legs each showing the share of
    their hits, shaded against the squad (the highest share is the strongest blue)
  - who carries the damage: 100 bullets, one per 1% of all the squad's damage, a row per player
  - team comps by role mix, each shown as a lineup of role letters (D D C I S)
  - agent pool: each player's ACS on every agent they've played, by role
  - clutches (1vX attempts and wins) and multi-kills (3K, 4K, aces)
  - spike sites: post-plant win rate on attack and retake rate on defence,
    per map and site, one side at a time

  Clutches, multi-kills and spike sites use each game's round-by-round record
  (kills, round winners, plants). The tracker keeps a compact copy of it for
  every 5-stack game; older games are filled in a few per sync.

  A "night" is a run of games with no break over 3 hours.

Only modes listed under `modes` count. The default is `competitive`, `unrated`
and `premier`; deathmatch and other non-5v5 modes are ignored so averages stay
comparable.

## How 5-stack detection works

HenrikDev's *stored matches* endpoint returns a player's own line for every
match the API has stored, and it costs one request per member. A match id that
shows up for **every** member, with everyone on the same team, is a 5-stack
game. Stored history can have holes, so when a match shows up for all but one
member the full match record is fetched to check whether the missing member was
in it too. Anything else is remembered as rejected so it is never re-checked.

## 5-stack vs. their other games

The same stored-match responses also contain every game each member played
without the full stack (solo queue, duos, 3- and 4-stacks). Those games, in the
tracked `modes`, are kept as each player's baseline, so this costs no extra API
calls. The Players page compares the two on ACS, kills / deaths / assists per
round, ADR, headshot %, K/D and win rate.

A difference is labelled **better** or **worse** when it is about two standard
errors or more (judged from how much the stat swings game to game on each side),
**slightly** better or worse between one and two, and *no real change* below
that. Nothing is judged until both sides have at least 5 games. Stats rise and
fall with winning, so part of a gap can reflect the stack's win rate rather than
the player.

A database created before this feature existed only holds 5-stack games; the
first sync after upgrading fetches everyone's full history once to fill in the
baseline.

## Match recap

The top of the **Matches** tab recaps one game: the latest by default, or any
game you pick from the list below it. (The Overview's *Last game* tile links
here too.)

- **Header:** result, score, map, mode, date, length, which game of the night it
  was and the night's record.
- **Highlights:** everything noteworthy, ranked so the best 6 show as cards and
  the rest sit under "more". Green is good, red is rough, purple is just odd:
  - *Records and near-records* in a player's 5-stack history: most / fewest
    kills, deaths, assists, highest / lowest ACS, ADR, HS%, K/D, ties, "2nd-best
    ever", "best ACS in 23 games", best game on an agent or a map.
  - *Firsts:* first game on an agent, first time playing a role, first ace.
  - *Rank changes:* ranked up, dropped, new peak rank.
  - *Milestones and streaks:* every 500 kills and 50 games per player, every 25
    games as a squad, top-fragging several games in a row, win and loss streaks
    (and snapping one), streaks on a map, first game or first win on a map.
  - *Round by round:* aces, 4Ks, repeated 3Ks, clutches from 1v2 to 1v5,
    comebacks and blown leads, shutouts and flawless halves, long round runs,
    both pistol rounds, retakes, first-kill control, knife kills, team kills, lots
    of Operator kills.
  - *Stat lines:* a carry (a third or more of the squad's damage), more assists
    than kills, no headshots or 40%+ headshots, 3+ K/D, barely dying, most kills
    with the least damage (and the reverse), a dead-even scoreboard, all five
    positive or negative.
  - *Squad records:* biggest win, heaviest loss, most squad kills, longest game,
    quickest win, overtime, surrenders.
  - *Forecast and betting surprises:* a player well outside their forecast from
    the Forecasts tab, winning as underdogs or losing as favourites, long shots,
    parlays, big wins, betting on yourself and cashing it, the full performance
    bonus.

  Records only count against games *before* this one, so an old game shows what
  was notable at the time. They need 10 earlier games, and 5 on an agent or map,
  so a player's third game can't set a record.
- **Scoreboard:** each player's line with ▲ / ▼ where a stat is well above or
  below their usual 5-stack game, plus damage share, first kills, big rounds
  (aces, 4Ks, 3Ks, clutches). Hover a number for their usual and their
  forecast.
- **Round by round:** every round won or lost, with spike plants, defuses, the
  first kill, multi-kills and clutches marked; hover for details.
- **Betting and rewards:** the bets settled on the game, who won and lost, the
  house's take, the best bets, and the game rewards paid.

## Forecasts: predicted vs actual

The **Forecasts** tab replays the odds engine over your history. For every
5-stack game, it predicts each player's kills, deaths and assists (per game,
which are the betting lines, and per round), ACS, ADR and headshot % using only
their *earlier* 5-stack games, weighted exactly like the player-prop lines
(recent games, and games on that map and agent, count more). The per-round
versions take game length out: a 26-round overtime game gives more kills than a
16-round stomp without anyone playing better.
Each prediction has three parts, all from the same smoothed distribution the
betting lines use:

- a **range** that should hold about 80% of games. It's cut at that
  distribution's 10th and 90th percentiles, so it's lopsided when the stat is: a
  few big games stretch the top more than the bottom;
- the **typical game** (the median, where a betting line sits), drawn as the tick
  on each bar;
- the **expected** value (the weighted average). "Average vs forecast" uses this,
  because against the median everyone would seem to beat the forecast on
  skewed stats like kills.

The page puts each prediction next to what actually happened:

- tiles for how often the player landed inside the range, beat it or fell short;
- a **game-by-game strip** of forecast ranges with the real result as a dot;
- a **map × role grid** (Duelist, Controller, Initiator, Sentinel) with the actual
  average against the expected average in each cell, shaded by how far they beat or
  missed it; hover a cell for the range and each agent, click it to show just
  those games in the strip.

A game gets a forecast once the player has 5 earlier 5-stack games; surrendered
games aren't forecast. For deaths, fewer counts as beating the forecast.

## How the odds work

For each player the history of 5-stack games is turned into a
recency-weighted sample (half-life `recency_half_life_games`, default 15 games).
If you pick an expected **map** or an expected **agent** per player on the Odds
page, matching games get extra weight (`map_weight_boost`, `agent_weight_boost`).

- **Player props (over/under):** kills, deaths, assists, ACS, ADR, headshot %.
  The line sits at the weighted median; the over/under probability comes from a
  Gaussian-kernel smoothed distribution of past games.
- **Custom lines:** "I think Loog gets 25 kills." Under the player props, pick a
  player, a stat, *at least* or *at most*, and a whole number, and you get odds
  for it: at least 25 is an over 24.5, at most 12 an under 12.5. It's priced from
  the same smoothed distribution as the board's own lines, with the same house
  edge, so a custom line at the board's number costs exactly what the board
  charges. It settles like any over / under, and can be a single or a parlay leg
  (one line per player and stat in a parlay). To keep it within reason, the side
  you bet needs between a 5% and a 90% chance: long shots top out around +1800,
  and near-certainties ("at least 5 kills") aren't offered. The card shows which
  numbers you can pick for each player and stat.
- **🔥 Hot streaks:** a pick with a flickering flame border (player props, top /
  bottom of the scoreboard, team markets) would have won each of the last 3 games
  or more in a row at today's line. Hover it for the length of the run (counted back
  up to 10 games). A refunded result, like a push or a surrender, is skipped.
  A roughly 50/50 pick (a 35–65% chance) that lost its last 3 or more instead
  gets a frosty border; long shots lose most games anyway, so they never do.
  Only markets with three or more picks (top / bottom of the scoreboard, margin,
  exact score) get frost: in an over/under or win/loss market the other side of
  a cold pick already has the flame.
  These are only labels: the odds don't change.
- **Celebrations:** a bet you win sets off confetti from your balance under the
  bet slip (gold, and more of it, for a long shot at +500 or longer), with a
  toast saying what paid. Your browser remembers the last win it showed you, so
  a bet that settles while the site is closed, or while you're on another tab,
  gets its confetti the next time you open Odds & Bets. The balance under the bet slip counts up or down when it changes,
  and a bet you place lands with a "Placed" stamp in **Your open bets**, the box
  under the custom line that lists just your own open bets.
- **Exact numbers:** the same card's *exactly* option ("Loog gets exactly 25
  kills") on kills, deaths and assists. The chance is the share of that same
  distribution that rounds to exactly N, with the double house edge of the
  many-outcome markets; it wins only if the final number matches. Exact numbers
  are always long shots, so they need at least a 2% chance (odds up to about
  +4700). On a surrender, an exact number the player had already passed loses and
  anything else is refunded. In a parlay it counts as the same market as the
  player's line on that stat.
- **"Who tops the scoreboard" markets:** six cards, each with a toggle for its
  counter market at the bottom of the scoreboard:

  | Top | Bottom |
  |---|---|
  | Top fragger (most kills) | Bottom fragger (fewest kills) |
  | Highest ACS | Lowest ACS |
  | Most assists | Fewest assists |
  | Most deaths | Fewest deaths |
  | Best HS % | Worst HS % |
  | Popped off | Got diff'd |

  "Popped off" / "Got diff'd" go to the player whose ACS is furthest above /
  below *their own* average over their earlier 5-stack games, so anyone can win
  them; they void if someone has no earlier games to compare against.
  Probabilities come from a Monte Carlo simulation that draws one game per
  player from their weighted history; each top market and its counter come from
  the same simulated games. A tie at the top (or bottom) refunds the stake.
- **Team markets:** match result, total rounds, and overtime (does the game go
  past 12–12?). Overtime is rare, so its odds come from your history shrunk
  toward a ~10% base rate, and surrendered games are left out of it.
- **Score markets:** rounds won and rounds lost by the squad (over / under, with
  overtime counting as 12+), **winning margin** (the squad wins by 1–2, 3–5 or
  6+; overtime is 1–2), and the **exact score** of a squad win (13–0 to 13–11).
  Margin and exact score are only offered on the squad winning: a loss loses
  them, and so does an overtime win for exact score. They all come from one model of the final score:
  each round is won with some chance, first to 13, and that chance varies from
  game to game. The model is tuned so its chance of winning matches the match
  result odds and its chance of reaching 12–12 matches the overtime odds as
  closely as it can, so none of these markets contradict each other. Winning
  margin and exact score have many picks each, so like the scoreboard markets
  they carry double the house edge. They only settle on first-to-13 games (a
  shorter mode refunds them).
- **Surrendered games are partial data.** Their kills, deaths and assists are
  scaled up to a full-length game (the median length of your completed games,
  or 22 rounds until there are 5 of them), and the game counts only as much as
  the share of a full game that was played. They are left out of the
  total-rounds line, and count normally toward the match-result odds.

Fair probabilities are then shaded by `house_edge` (default 5%, doubled for the
multi-way markets), exactly like a sportsbook's vig, and shown as American or
decimal odds. Players with fewer than a few games borrow the team's pooled
distribution at low weight and are flagged *low confidence*.

## Betting rules

- Every bettor has their own account: pick a name and a personal betting
  password in the bet slip (*Create account*). From then on only someone signed
  in with that password can bet as that name or cancel its bets. Accounts
  start with `starting_balance` credits (default 1000).
- Forgot a password? With `admin_password` set, the commissioner can free the
  name again via `POST /api/bettor/clear-password` with the `X-Admin-Password`
  header, after which it can be re-claimed with a new password.
- Bets are on the **next 5-stack game** that starts after the bet is placed, no
  matter which map ends up being played. Odds are locked when you place the bet.
  Riot's start time is when the match launches, so a bet placed within
  `bet_grace_minutes` (default 2) of a game starting, e.g. while loading in or
  during round 1, still counts for that game. Later bets carry over to the
  following game.
- Settlement happens automatically during the sync that records that game.
  Landing exactly on a line, a tie for a "tops the scoreboard" market, or a draw
  for the match-result market refunds the stake (void).
- A bet can be cancelled for a full refund only within `bet_cancel_minutes`
  (default 1) of placing it, enough to fix a misclick but not to back out of a
  game that's going badly. The commissioner can still cancel any open bet with
  the admin password.
- **Surrenders (forfeits).** A game that ends before either team reaches the
  rounds needed to win (13 in competitive, unrated and premier) was
  surrendered. The **match result stands** and match-result bets settle as
  usual. Every other bet settles only if it was **already decided** when the
  game stopped: an over on a counting stat (kills, deaths, assists, total
  rounds) that had already cleared its line wins, and the matching under
  loses; an overtime bet settles only if the game had already reached 12–12;
  rounds won / lost settle the same way as total rounds (an over already
  cleared wins). Exact score and winning margin are refunded, since the final
  score never happened. Anything else is refunded, including per-round stats (ACS, ADR,
  headshot %) and top/bottom-of-the-scoreboard markets, which could still have
  swung. When the full match record is available, its winner flag decides
  who won, even if the surrendering team was ahead on rounds. Parlay legs
  follow the same rule, leg by leg (see Parlays).
- **Remakes.** A game that ends within the first 4 rounds is treated as a
  remake or an abandoned lobby, not a game: it isn't recorded, open bets
  carry over to the next game, and it pays no rewards.
- Bets are shown as **slips**. On the Odds & Bets tab, open bets are grouped
  card by card under the bettor who placed it, with each card's bets and total
  wagered in its corner, so it's obvious at a glance who has what riding on
  the next game. Every bettor's colour is their squad member's colour, and odds
  follow the American / decimal switch.
- The **Bettors** tab ranks everyone by balance, with profit against the
  starting bankroll, record, win rate, ROI and open stakes. Under the rankings,
  a **betting report card** shows each bettor's ROI by market type, and how they
  do betting on themselves vs on others, followed by each bettor's **profit over
  time**. Below that, **settled bets** show one game at a time: the most recent
  by default, or any earlier game picked from the dropdown. The game is headed by
  the result and the squad's totals, with each player's bets on it in their own
  card (bets, wagered and net). At the
  bottom of the page, **Are the odds right?** checks the odds against results:
  picks are grouped by the chance the odds gave them, and each group shows how
  often it actually won, overall and per market type ("about right", "too
  generous" or "too stingy", once there are at least 10 settled picks).

## Parlays

Pick 2 to 10 selections (any mix of player props, "tops the scoreboard" and
team markets, one pick per market) and toggle the bet slip to **Parlay** to
combine them into a single all-or-nothing bet at combined odds, instead of
placing them as separate singles. In the slip, the toggle only appears once
there are 2+ picks.

- All legs settle off the same next 5-stack game. If every leg wins, the payout
  is the stake times the combined odds locked in at placement.
- **Legs that decide each other are refused.** Team legs are all settled from
  the final score, so a pair where one can only win when the other does
  ("Exact score 13–5" and "Win", "Win by 6+" and "Win", "Overtime: Yes" and
  "Rounds won over 10.5") or where both can never win ("Loss" and "Win by 1–2")
  can't share a parlay. The slip says which pair and greys out the button.
- **Legs that tend to land together are priced together.** The combined odds
  are normally the legs' odds multiplied, which assumes they're unrelated. The
  slip replays every leg at today's line on the last 60 games; when legs won
  together clearly more often than chance (a player's kills and ACS overs, an
  over on kills and the same player topping the scoreboard, total rounds over
  with everyone's kills overs), they're marked **linked** and the odds are cut by
  how much more often they all landed together. A few games can't swing it
  much (the estimate is pulled toward "unrelated"), odds are only ever cut,
  never raised, and a parlay never pays less than its longest leg alone. The
  ticket shows what the odds were cut from.
- If any leg loses, the whole parlay loses.
- If a leg is voided (push, tie for the top, a draw) it's dropped with no
  effect: the payout is recalculated from the odds of the legs that stood
  (priced together again if some of them are linked). If every leg is voided,
  the stake is refunded.
- If the game is **surrendered**, each leg is judged by the surrender rule
  above: the match-result leg stands, a leg that was already decided keeps its
  result, and an undecided leg is dropped like any other void leg. So a leg
  already lost (say an under that was already beaten) still loses the parlay,
  and a parlay where nothing was decided is refunded.
- The **Open bets** / **Settled bets** slips show each leg of a parlay
  underneath the ticket, with a ✓ / ✗ / ↺ per leg once it settles.
- *Reset season* (under the Bettors tab's rankings) ends the season. It asks
  you to type `RESET` (and the admin password, if one is set), and says exactly
  what will happen. The season's final standings, every bet and every game
  reward are then saved under **Past seasons** on the Bettors tab, before
  everyone goes back to the starting balance and bets and rewards are cleared.
  Bets still open at that moment are closed. Nothing is lost: past seasons keep
  their full history. Set `admin_password` so that only the commissioner can
  end a season.

## Game rewards

Playing earns credits too. For every 5-stack game recorded, won or lost, each
squad member's bettor account gets:

- **`game_reward`** (default 50), plus `win_reward` on top for a win (default
  0, so wins and losses pay the same unless you set it), and
- a **performance bonus** of up to `performance_bonus_max` (default 150). The
  bonus is the share of your *previous 5-stack games* that this game's ACS
  beats, so you're measured against how you usually play with the squad. Beat
  80% of them and you get 120; set a new 5-stack best and you get the full 150.
  With fewer than 5 earlier 5-stack games to compare against, the bonus is half
  (75).
  Bonuses are paid in steps of 5 credits (rounded to the nearest 5).

So a game pays between 50 and 200 credits per player with the defaults.

Rewards go to the member's bettor account. The first of these that applies wins:

1. `"bettor"` on that member in `config.json`
2. the member's nickname in `bettor_names.json` (committed to the repo), e.g.
   `{ "it": "Kikii", "fat": "fatty" }`
3. the member's `nickname` (or Riot name) itself

Names match regardless of case. If no such account exists yet, one is created
with the starting balance and left unclaimed; the player claims it by signing up
with that name.
Only games played after rewards were switched on pay out, so upgrading doesn't
pay for past games. The Bettors tab shows rewards in their own column and keeps
profit and ROI betting-only.

## Going online (share it with the squad)

The server can publish itself through a Cloudflare Tunnel, so your friends can
open it from anywhere while it keeps running on your PC.

1. Make sure `site_password` is set in `config.json` (one is generated for you
   on the first setup). Everyone logs in with it once per browser. Without a
   password the tunnel refuses to start.
2. Run `run-online.bat` (or `python server.py --tunnel`). The first time,
   `cloudflared` is downloaded into `tools/` automatically.
3. Open Setup (the ⚙ button, top right). The public link is shown under *Online access* with a
   Copy button, and it is printed in the console too.

That is a *quick tunnel*: free, no account, HTTPS. The address is random and
changes every time the server restarts, so re-share it after a reboot.

**Permanent address (optional).** With a domain on Cloudflare: Zero Trust
dashboard → Networks → Tunnels → *Create a tunnel* → add a public hostname that
points at `http://localhost:8080` → copy the token. Then set
`"tunnel": "token"`, `"tunnel_token": "<token>"` and
`"tunnel_hostname": "stack.yourdomain.com"` in `config.json`. You can also lock
that hostname behind Cloudflare Access (Zero Trust → Access → Applications) so
only your friends' email addresses get through; it is free for up to 50 users.

**Start with Windows.** Press Win+R, run `shell:startup`, and drop a shortcut
to `run-online.bat` in that folder. The server and the tunnel then start every
time you log in.

**What protects the site**

- Everything except the login page needs the password cookie (30 days, HttpOnly).
- Eight wrong passwords from one address lock it out for ten minutes.
- `admin_password` (optional) is asked for on *Reset season* and lets the
  commissioner cancel anyone's bet or reset a bettor's password.
- Keep `host` at `127.0.0.1`. The tunnel talks to the server locally and nothing
  is opened on your router.
- Bettors sign in with a personal password (hashed with PBKDF2), so the shared
  site password only grants viewing; it cannot be used to bet as someone else.

## Configuration reference (`config.json`)

| Key | Default | Meaning |
| --- | --- | --- |
| `api_key` | – | HenrikDev key. Can also be given as the `HENRIK_API_KEY` environment variable. |
| `region` | `na` | Riot affinity of the squad: `na`, `eu`, `ap`, `kr`, `latam`, `br`. |
| `platform` | `pc` | Game platform (`pc` or `console`) for the HenrikDev endpoints that take one. Reserved: nothing uses it yet. |
| `members` | – | List of `"Name#TAG"` strings or `{ "riot_id": "Name#TAG", "nickname": "Matt", "bettor": "Matty" }` objects. `bettor` (optional) is the betting account that receives this member's game rewards; it defaults to the nickname. |
| `modes` | competitive, unrated, premier | Modes that count. Empty list = every mode. |
| `poll_interval_minutes` | 10 | How often to check for new games. |
| `poll_size` | 40 | Stored matches fetched per member on a regular sync (the first sync fetches everything). |
| `fetch_match_details` | true | Fetch full match records to verify holes and enrich games. |
| `details_per_sync` | 6 | Cap on enrichment fetches per sync, to stay well inside the rate limit. |
| `min_request_interval_s` | 1.5 | Minimum spacing between API requests. |
| `host` / `port` | `127.0.0.1` / `8080` | Bind address. Use `0.0.0.0` to let friends on your LAN open the site. |
| `open_browser` | true | Open the site automatically on start. |
| `site_password` | – | Shared squad password. Required before the tunnel will start. |
| `admin_password` | – | Optional second password asked for on *Reset season*. |
| `tunnel` | `off` | `off`, `quick` (random trycloudflare.com address) or `token` (Cloudflare dashboard tunnel). `--tunnel` and `--no-tunnel` override it. |
| `tunnel_token` / `tunnel_hostname` | – | For `token` mode: the dashboard token and the hostname you assigned. |
| `house_edge` | 0.05 | Bookmaker margin applied to fair probabilities. |
| `recency_half_life_games` | 15 | Older games count for less; this many games back a game has half weight. |
| `map_weight_boost` / `agent_weight_boost` | 2.5 / 2.0 | Extra weight for games matching the chosen map / agent. |
| `simulations` | 4000 | Monte Carlo draws for the "tops the scoreboard" markets. |
| `starting_balance` | 1000 | Credits for a new bettor. |
| `bet_grace_minutes` | 2 | A bet placed this soon after a game starts still counts for that game. `0` means only bets placed before the start. |
| `bet_cancel_minutes` | 1 | How long after placing a bet its bettor can still cancel it. The admin can cancel open bets any time. |
| `game_reward` | 50 | Credits each member earns per 5-stack game, win or loss. `0` turns it off. |
| `win_reward` | 0 | Extra credits each member earns on top for a win. |
| `performance_bonus_max` | 150 | Most a member can earn per game for beating their own baseline. `0` turns it off. |

Command-line flags: `--demo`, `--no-browser`, `--port=8090`, `--tunnel`, `--no-tunnel`,
`--config=path/to/other.json`.

## Rate limits

A Basic key allows about 30 requests per minute, and HenrikDev also counts the
Riot requests it makes in the background to fill its cache. A regular sync is
one request per member plus a handful of match-detail fetches, spaced 1.5 s
apart; the client reads the rate-limit headers and backs off automatically on
`429`. If a sync fails you will see why on the Setup page (⚙) and in the console.

## Project layout

```
server.py              launcher: python server.py [flags]
run.bat, run-online.bat  double-click launchers (the second adds --tunnel)
config.example.json    template copied to config.json on first run
bettor_names.json      squad nickname -> bettor account name, for game rewards
fivestack/             the backend package
  app.py               HTTP server + JSON API routes (stdlib http.server), command-line flags
  config.py            file locations, config.json loading and validation
  auth.py              site / admin passwords and bettor sessions (signed cookies)
  tracker.py           member resolution, 5-stack detection, background polling
  henrik.py            HenrikDev API client
  db.py                SQLite schema and queries (data/tracker.db)
  stats.py             aggregation (overall / per agent / per map / team, stack vs. other games)
  insights.py          datasets for the Charts tab (sessions, comps, damage share, ...)
  forecasts.py         Forecasts tab: each game replayed against the odds engine's prediction
  recap.py             Matches tab recap: scoreboard, round by round, betting and highlights
  timeline.py          round-by-round records: clutches, multi-kills, spike sites
  odds.py              odds engine
  gamestate.py         how a game ended: complete, surrendered, or a remake
  bets.py              betting ledger and settlement
  tunnel.py            Cloudflare Tunnel runner (downloads cloudflared into tools/)
  demo_seed.py         synthetic data for --demo
web/                   index.html, app.js, viz.js (charts), bets.js (betting UI), recap.js (match recap), style.css,
                       assets/ (onkey-logo.png, the top-left logo; greg.png and greg-logo.png for Greg Mode;
                       onkey.png, the logo's full-size original) (no build step)
tests/selftest.py      offline test of detection, stats, odds and settlement
data/, tools/          created at runtime (database, cloudflared); not committed
```

Run `python tests/selftest.py` to check the backend end-to-end without touching the API.
GitHub runs it automatically (on Python 3.10 and 3.12, plus a JavaScript syntax
check) for every pull request and every push to `main`
(`.github/workflows/selftest.yml`).

[`docs/DATA.md`](docs/DATA.md) lists every piece of data available: what the HenrikDev API returns (with field
structures from real responses), what the tracker stores, and what the website's `/api/*` endpoints serve.

## Notes and limits

- The API is unofficial and rate-limited; keep the poller interval reasonable
  and do not point several instances at the same key.
- Stored history only contains matches that HenrikDev has seen for at least one
  player, so very old games may be missing. Everything from the moment the
  tracker starts running is captured.
- Data lives in `data/tracker.db` (SQLite). Delete it to start over; the next
  sync rebuilds the history.
