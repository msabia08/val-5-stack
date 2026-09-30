# 5-Stack Tracker

A small local website that follows your Valorant 5-stack through the
[HenrikDev API](https://docs.henrikdev.xyz). It only counts games where **all
five of you were on the same team**, keeps per-game averages overall, per agent
and per map for each player, and turns that history into **betting lines for
your next game** that the squad can bet on with virtual credits.

No Node, no build step, no third-party packages: Python 3.10+ and a browser.

**Screens and theme.** The site is built around a 1920×1080 desktop screen;
other sizes (phones, tablets, small laptops) aren't supported. It opens in dark
mode whatever your computer's own light/dark setting is. Light mode (and any
theme bought in Onkey's Shop) is one click on ◐ in the top bar, and the site
remembers your pick.

**Getting around.** The top bar has 🏠 **Overview**, then three menus: 📊
**Stats** (Players, Forecasts, Charts, Matches), 🎲 **Betting** (**Place bets**:
the odds for the next game and your bet slip; **Standings**: rankings, results,
seasons, credits and rewards) and 🐒 **Onkey's** (Shop, Arcade, Monkeys). A menu's
button shows the page you're on.

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
  from their games outside the stack (see below). The comparison is still worked
  out, but it's hidden on the Players page for now.
- **Each match:** map, mode, score, date, each member's line, and (when the full
  match record is fetched) game length, ranks and whether all five shared a party.
  The **Matches** tab opens with a recap of the latest game (see below); click
  any game in the list, or use *Older* / *Newer*, to recap another.
- **Overview:** the front page, all on one screen. Across the top: the squad's
  record, its form (the last 10 results; click one for its recap), last night's
  record, and the **next game**: the match result odds plus up to two picks on a
  hot streak, which add to your bet slip like on Place bets. Then the last game
  with its top five highlights (linking to the full recap); betting (who leads,
  and your own place when you're signed in, and the standout bets this season:
  biggest win and loss, longest odds won, shortest odds lost); who's
  trending (whether each player is heating up, cooling off or steady, from
  their ACS over the last 5 games against the 10 before; their ACS and K/D over
  those games, each with the change from the 10 before; and an ACS sparkline, all players on one scale, with the last 5
  games shaded. Click a player to open them on Players); and
  map performance (win rate per map, best first, green from 50% and red below,
  with the average round difference; maps with 3 or fewer games are faded, and
  clicking one opens its games on Matches). Two more cards round out the grid:
  **Riding on the next game** (how many credits are on the next game and from
  how many bettors, the pick with the most credits behind it, and the three
  biggest open bets) and **Onkey's** (the top collector, the newest pranks in
  play with who pranked whom and how long they last, and each arcade game's best
  score).
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
calls. The comparison covers ACS, kills / deaths / assists per round, ADR,
headshot %, K/D and win rate. It's hidden on the Players page for now
(`PLAYER_TABLE_OTHER_GAMES` and `PLAYER_CARD_OTHER_GAMES` in `web/app.js` bring it
back), and `/api/stats` still returns it for each player as `deviation`.

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
game you pick from the list below it. (The Overview's *Last game* card links
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
    parlays, big wins, the full performance bonus.

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

The page starts with the **next game**: the same forecast made from every game so
far. It's drawn as a range on a number line with the typical game and the
current betting line, and the Over / Under buttons add that line to your bet
slip (kills, deaths, assists and headshot %, the stats with a line).

Then it puts each past prediction next to what actually happened:

- tiles for how often the player landed inside the range, beat it or fell short
  (for the map picked below, when one is);
- a **game-by-game chart** of forecast ranges with the real result as a dot. A
  miss has a line from the edge of the range, faint dashed lines separate nights,
  and clicking a game opens its recap on the Matches tab;
- a **bar per map** for how far the player's average landed from the expected
  average, better to the right and worse to the left (paler with fewer games).
  With more than one role (Duelist, Controller, Initiator, Sentinel) there's a
  chip for each. Hover a map for the range and each agent; click it to show just
  those games in the chart and tiles.

A game gets a forecast once the player has 5 earlier 5-stack games; surrendered
games aren't forecast. For deaths, fewer counts as beating the forecast.

## How the odds work

For each player the history of 5-stack games is turned into a
recency-weighted sample (half-life `recency_half_life_games`, default 15 games).
The Place bets page always prices the next game from every map and agent. (The
odds engine can still weight one map or agent up, `map_weight_boost` and
`agent_weight_boost`, for anything that asks it to; the page no longer does.)
American or decimal odds are picked with the toggle above the bet slip.

- **Player props (over/under):** kills, deaths, assists and headshot %. ACS
  isn't a prop, but it's still bet on through *Top and bottom of the scoreboard*
  (Highest ACS, Popped off and their counters).
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
- **Celebrations:** a bet you win sets off confetti from your credits at the top
  of the page (gold, and more of it, for a long shot at +500 or longer), with a
  toast saying what paid. Your browser remembers the last win it showed you, so
  a bet that settles while the site is closed, or while you're on another tab,
  gets its confetti the next time you look. Your credits count up or down when they change,
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
- **Team markets:** the match result comes first: Win and Loss either side of a
  bar split by each side's chance, with the squad's last five results (W / L,
  newest on the right) under the middle of the bar. Then each market is a
  question card (hover a question for how the market works):
  - **By how much?** the margin, from losing by 6+ to winning by 6+
  - **Win the pistol?** round 1
  - **Ahead at half-time?** after round 12 (6–6 isn't ahead)
  - **Overtime?** does the game go past 12–12? It's rare, so its odds come from
    your history shrunk toward a ~10% base rate; surrendered games are left out.
  - **Anyone ace?** one of the five kills all five enemies in one round
  - **Comeback from 5 down?** the squad falls 5 or more rounds behind and still
    wins (a long shot)
  - **Flawless rounds?** over / under on rounds won with nobody in the squad dying
  - **Final score?** every result in one row, from a 0–13 loss through overtime
    to a 13–0 win, each with a bar for how likely it is.

  The pistol, half-time, ace, comeback and flawless markets are read from each
  game's round-by-round record, so they appear once 5 games have one, and they're
  priced from your recent games like the rest. If the record isn't available
  for the game they settle on, they're refunded. (Total rounds, and rounds won /
  lost over / unders, used to be offered too; they're gone, but bets already
  placed on them still settle.)
- **Score markets:** **margin** (lose or win by 1–2, 3–5 or 6+; overtime is
  1–2 either way) and the **exact score** (every regulation score either way,
  plus an overtime win and an overtime loss as their own picks), so one pick
  wins every game. They all come from one model of the final score:
  each round is won with some chance, first to 13, and that chance varies from
  game to game. The model is tuned so its chance of winning matches the match
  result odds and its chance of reaching 12–12 matches the overtime odds as
  closely as it can, so none of these markets contradict each other. Margin and
  exact score have many picks each, so like the scoreboard markets
  they carry double the house edge. They only settle on first-to-13 games (a
  shorter mode refunds them).
- **Surrendered games are partial data.** Their kills, deaths and assists are
  scaled up to a full-length game (the median length of your completed games,
  or 22 rounds until there are 5 of them), and the game counts only as much as
  the share of a full game that was played. They count normally toward the
  match-result odds and are left out of the overtime odds.

Fair probabilities are then shaded by `house_edge` (default 5%, doubled for the
multi-way markets), exactly like a sportsbook's vig, and shown as American or
decimal odds. Players with fewer than a few games borrow the team's pooled
distribution at low weight and are flagged *low confidence*.

## Betting rules

- Every bettor has their own account: click **Sign in** at the top right, then
  pick a name and a personal betting password (*Create account*). The same menu
  signs you in on another device, and once you're signed in it has your
  profile, *Change password* and *Sign out*. From then on only someone signed
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
  rounds won / lost (older bets; no longer offered) settle the same way as
  total rounds (an over already cleared wins). The pistol round is always
  decided; half-time only if round 12 was played; an ace or a comeback settles
  if it already happened (a comeback also needs the win to stand), and "no ace"
  is refunded; flawless rounds settle like a count (an over already cleared
  wins). Exact score and margin are refunded, since the final score never
  happened. Anything else is refunded, including per-round stats (ACS, ADR,
  headshot %) and top/bottom-of-the-scoreboard markets, which could still have
  swung. When the full match record is available, its winner flag decides
  who won, even if the surrendering team was ahead on rounds. Parlay legs
  follow the same rule, leg by leg (see Parlays).
- **Remakes.** A game that ends within the first 4 rounds is treated as a
  remake or an abandoned lobby, not a game: it isn't recorded, open bets
  carry over to the next game, and it pays no rewards.
- Bets are shown as **slips**. On Place bets, open bets are grouped
  card by card under the bettor who placed it, with each card's bets and total
  wagered in its corner, so it's obvious at a glance who has what riding on
  the next game. Every bettor's colour is their squad member's colour, and odds
  follow the American / decimal switch.
- **Standings** ranks everyone by balance, with profit against the
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
- **Legs that decide each other are refused.** Match result, overtime, margin
  and exact score are all settled from the final score, so a pair where one can
  only win when the other does
  ("Exact score 13–5" and "Win", "Win by 6+" and "Win", "Exact score 13–5" and
  "Win by 6+") or where both can never win ("Loss" and "Win by 1–2")
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
- *Reset season* (under the rankings on Standings) ends the season. It asks
  you to type `RESET` (and the admin password, if one is set), and says exactly
  what will happen. The season's final standings, every bet, every game
  reward and every transfer are then saved under **Past seasons** on the
  Standings page, before everyone goes back to the starting balance and bets,
  rewards and transfers are cleared.
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
pay for past games. Standings shows rewards in their own column and keeps
profit and ROI betting-only.

## Onkey's Shop and the Monkeys

Bananas 🍌 are a second currency that runs alongside credits and only buys
cosmetics and pranks. They never turn back into credits, and spending them
never changes a balance, a bet or the leaderboard.

**Earning:** every credit you *gain* pays `banana_rate` bananas (default 0.1,
so 10 credits = 1 banana). That covers a won bet's profit (payout minus stake)
and every game reward. Losing bets never take bananas away, so a season's
bananas are always exactly your credits won that season ÷ 10: a straight line.
Bananas are paid on each sync, right after bets settle and rewards are paid.
Every account also starts with `starting_bananas` (50), which isn't counted as
earned. A season reset takes every wallet back to zero with the credits, then
hands everyone a fresh 50; items you bought stay yours.

**The Onkey's Shop tab** sells one item per slot, worn as soon as you buy it:

| Slot | What it changes | Price |
| --- | --- | --- |
| Name colour | how your name is written on rankings, tickets and the Monkeys page (12, from Ripe to an animated Rainbow) | 120-1200 |
| Badge | an emoji next to your name, and your avatar (17) | 40-500 |
| Title | a line under your name on the Monkeys page and your profile (17) | 100-800 |
| Profile banner | the header of your profile (9) | 300-600 |
| Ticket style | your bet tickets on Place bets and Standings, for everyone | 300-600 |
| Win celebration | what bursts out of your balance when a bet wins | 250-350 |
| Site theme | unlocks Greg Mode, Onkey Mode, Jungle Mode, Sakura, Midnight, Terminal or Synthwave (dark and light are free). With only those two, ◐ toggles between them; once you own a theme, ◐ opens a picker | 750-1200 |

Click any item for a **preview** of your profile card and bet tickets as they
are now and with the item (a theme shows a small page in its colours, and a
celebration can be played). Owned items can be worn or taken off at any time.

**Monkey business** items are used on someone else and wear off by
themselves. Everyone sees who sent what.

| Prank | What it does | Lasts | Price |
| --- | --- | --- | --- |
| Shrink Ray | their name goes tiny | 3 games | 35 |
| Banana Peel | their name slips and wobbles | 3 games | 40 |
| Upside Down | their name is flipped on its head | 3 games | 45 |
| Clown Makeup | their badge and avatar become 🤡 | 3 games | 50 |
| Smoke Screen | their name is blurred until you hover it | 3 games | 55 |
| Jinx | frost on their name and bet tickets | 3 games | 60 |
| Glitter Bomb | sparkles on their name and bet tickets | 3 games | 70 |
| Bounty | a "wanted" strip on their tickets and a poster on their profile, with your name | 3 games | 75 |
| Heckle | a speech bubble you write (60 characters) on their bet tickets | 3 games | 30 |
| Wall Note | a note you write (80 characters) pinned on their profile | 3 days | 25 |
| Nickname | a nickname you write (20 characters) in quotes after their name | 24 hours | 60 |
| Title Swap | a title you write (24 characters) replaces theirs | 24 hours | 80 |

"3 games" means until three 5-stack games have started since, and a week at
most.

**The top bar** shows your credits, your bananas and your profile chip (badge
and name) on every page. The credits open Standings, the bananas open the shop,
and the profile chip opens your account menu (your profile, change password,
sign out). Signed out, only the chip shows, saying Sign in, and it opens the
sign-in form. The bet slip holds only your picks.

**The Monkeys tab** ranks every bettor by the bananas spent on their collection,
with their unspent bananas and what they earned this season. Click anyone (or a
name on the Bettors rankings) for their profile: banner, badge, title, what
they're wearing, their whole collection against the catalogue, their bet ticket
style, wall notes and the pranks they've sent and received.

## Onkey's Arcade

Three small games on the **Onkey's Arcade** tab. Each play costs **5 bananas**,
like a quarter in a machine, and the only prize is a place on that game's
high-score board (each player's best, all time; kept through season resets).
Bananas are never paid back and credits are never touched.

| Game | How it plays |
| --- | --- |
| Banana Catch | Move Onkey (← → / A D, or drag) to catch falling bananas; golden ones are worth 100. Dodge the falling Gregs. 60 seconds, 3 lives, combos up to x3. |
| Onkey Says | A rhythm game on Onkey's song: each of its 11 sung syllables is a note. Hit ← ↓ → (or A S D, or tap the lanes) as it reaches the ring, and hold the long last note. Three verses, each faster and squeakier (1x, 1.15x, 1.3x), with combo multipliers up to x4. |
| Spike Dash | An endless runner: jump (Space / ↑ / tap) and double jump over planted spikes, grab bananas, and see how far you get. It keeps speeding up. |

**Sound:** Onkey's song (`web/assets/onkey-song.wav`, the two recordings back to
back) is always played whole: it's the Onkey Says track, the reward when you
set a new personal best, and the jukebox button on the arcade sign. Everything
else is made in the browser: a coin drop, blips, and a bongo chiptune in C♯
minor, the key Onkey sings in. The sound on/off switch is remembered.

Quitting part-way (Esc or Quit) ends the game and the score so far still
counts. Scores are sent back with a one-time token from the paid play, and the
server turns down any score a game couldn't reach in the time it ran.

## Sending credits

Bettors can pay each other: settle a side bet, pay off a lost argument, spot a
friend who went broke. In the Standings page's sidebar (next to Game rewards),
**Send credits** takes a recipient (any bettor account, claimed or not), an
amount (at least 1, no more than your balance) and an optional note of up to 80
characters. A confirm line spells out who gets how much before anything moves,
because there's no undo.

- Credits move straight from one balance to the other, in one step, so two
  sends at once can't take you below zero.
- Transfers don't count as betting profit, ROI or record. The Rankings show
  each bettor's net transfers (received minus sent, generosity tax included)
  in their own column.
- The recipient gets a toast the next time they open the site ("Matt sent you
  50 credits: …"), and the card lists this season's transfers.
- A season reset archives transfers with the rest of the season.

### The generosity tax

*The generous monkey gets rewarded.* Send someone **250 or more** credits and
you collect the **generosity tax**: **10%** of the winnings (payout minus
stake) on their next winning bet. The person you sent to pays it out of that
win.

- "Next winning bet" means the next game on which one of their bets wins. If
  several of their bets win on that game, the biggest winner is the one taxed.
- You can have only one tax waiting on each person. Sending them more before
  it's paid doesn't add a second one.
- If several people sent them 250+, each collects their 10% from that same win.
- It's shown everywhere it matters: the banner on the Send credits card, the
  confirm line before you send, a tag on each transfer ("10% tax on Matt's
  next win", then "collected +40"), a note on the taxed bet's ticket saying
  where the money went, and a toast for the sender when it's paid.
- The rate and minimum are `TAX_RATE` and `TAX_MIN_TRANSFER` in
  `fivestack/bets.py`. A tax still waiting when the season is reset is dropped.

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
| `starting_bananas` | 50 | Bananas every account starts each season with (new accounts get them straight away). Not counted as earned. `0` turns it off. |
| `banana_rate` | 0.1 | Bananas paid per credit gained (a won bet's profit, a game reward) for Onkey's Shop. 0.1 = 1 banana per 10 credits. `0` stops paying bananas. |

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
  bananas.py           Onkey's Shop: bananas earned from credit gains, the catalogue, buying and wearing items
  arcade.py            Onkey's Arcade: paid plays, score checks and high-score boards
  tunnel.py            Cloudflare Tunnel runner (downloads cloudflared into tools/)
  demo_seed.py         synthetic data for --demo
web/                   index.html, app.js, viz.js (charts), bets.js (betting UI), shop.js (Onkey's Shop and Monkeys), arcade.js (Onkey's Arcade),
                       recap.js (match recap), style.css,
                       assets/ (onkey-logo.png, the top-left logo; greg.png and greg-logo.png for Greg Mode;
                       onkey.png, the logo's full-size original; onkey-song.wav, Onkey's song) (no build step)
tests/selftest.py      offline test of detection, stats, odds and settlement
data/, tools/          created at runtime (database, cloudflared); not committed
```

Run `python tests/selftest.py` to check the backend end-to-end without touching the API.
GitHub runs it automatically (on Python 3.10 and 3.12, plus a JavaScript syntax
check) for every pull request and every push to `main`
(`.github/workflows/selftest.yml`).

[`docs/DATA.md`](docs/DATA.md) lists every piece of data available: what the HenrikDev API returns (with field
structures from real responses), what the tracker stores, and what the website's `/api/*` endpoints serve.

[`docs/DESIGN.md`](docs/DESIGN.md) is the design guide for the website: the 1920×1080 dark-first target, layout,
colour tokens, type, the shared components, wording, and how to check a UI change.

## Notes and limits

- The API is unofficial and rate-limited; keep the poller interval reasonable
  and do not point several instances at the same key.
- Stored history only contains matches that HenrikDev has seen for at least one
  player, so very old games may be missing. Everything from the moment the
  tracker starts running is captured.
- Data lives in `data/tracker.db` (SQLite). Delete it to start over; the next
  sync rebuilds the history.
