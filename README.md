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
- **Visualizations tab:** charts built from all of the above. Each has a
  one-line takeaway, hover details and a table view:
  - record in close games vs. blowouts, win rate after a win vs. after a loss,
    and first game of the night vs. later games
  - form over time (rolling 10-game win rate plus every game's round margin)
  - when you win: day of week × time of day, in your local time
  - whether the squad fades later in a night
  - each player's ACS on each map against their own average
  - who swings results (ACS in wins vs. losses)
  - aim profile (head / body / legs)
  - share of team damage, game by game
  - team comps by role mix
  - bettor profit over time

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

## How the odds work

For each player the history of 5-stack games is turned into a
recency-weighted sample (half-life `recency_half_life_games`, default 15 games).
If you pick an expected **map** or an expected **agent** per player on the Odds
page, matching games get extra weight (`map_weight_boost`, `agent_weight_boost`).

- **Player props (over/under):** kills, deaths, assists, ACS, ADR, headshot %.
  The line sits at the weighted median; the over/under probability comes from a
  Gaussian-kernel smoothed distribution of past games.
- **"Who tops the scoreboard" markets:** top fragger, highest ACS, most
  assists, most deaths, best headshot %. Probabilities come from a Monte Carlo
  simulation that draws one game per player from their weighted history.
- **Team markets:** match result and total rounds.

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
- Settlement happens automatically during the sync that records that game.
  Landing exactly on a line, a tie for a "tops the scoreboard" market, or a draw
  for the match-result market refunds the stake (void).
- Pending bets can be cancelled for a full refund until the game is recorded.
- The **Bettors** tab ranks everyone by balance, with profit against the
  starting bankroll, record, win rate, ROI, open stakes and recent results.
- *Reset season* on the Odds page puts everyone back to the starting balance
  and clears all bets and game rewards.

## Game rewards

Playing earns credits too. For every 5-stack game recorded, won or lost, each
squad member's bettor account gets:

- **`game_reward`** (default 250), plus `win_reward` on top for a win (default
  0, so wins and losses pay the same unless you set it), and
- a **performance bonus** of up to `performance_bonus_max` (default 250). The
  bonus is the share of your *previous 5-stack games* that this game's ACS
  beats, so you're measured against how you usually play with the squad. Beat
  80% of them and you get 200; set a new 5-stack best and you get the full 250.
  With fewer than 5 earlier 5-stack games to compare against, the bonus is half
  (125).
  Bonuses are paid in steps of 5 credits (rounded to the nearest 5).

So a game pays between 250 and 500 credits per player with the defaults.

The account is the one named after the member's `nickname` (or Riot name), or
the one set with `"bettor"` on that member in `config.json` if they bet under a
different name. If no such account exists yet, one is created with the starting
balance and left unclaimed; the player claims it by signing up with that name.
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
3. Open the Setup page. The public link is shown under *Online access* with a
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
| `game_reward` | 250 | Credits each member earns per 5-stack game, win or loss. `0` turns it off. |
| `win_reward` | 0 | Extra credits each member earns on top for a win. |
| `performance_bonus_max` | 250 | Most a member can earn per game for beating their own baseline. `0` turns it off. |

Command-line flags: `--demo`, `--no-browser`, `--port=8090`, `--tunnel`, `--no-tunnel`,
`--config=path/to/other.json`.

## Rate limits

A Basic key allows about 30 requests per minute, and HenrikDev also counts the
Riot requests it makes in the background to fill its cache. A regular sync is
one request per member plus a handful of match-detail fetches, spaced 1.5 s
apart; the client reads the rate-limit headers and backs off automatically on
`429`. If a sync fails you will see why on the Setup page and in the console.

## Project layout

```
server.py              launcher: python server.py [flags]
run.bat, run-online.bat  double-click launchers (the second adds --tunnel)
config.example.json    template copied to config.json on first run
fivestack/             the backend package
  app.py               HTTP server + JSON API routes (stdlib http.server), command-line flags
  config.py            file locations, config.json loading and validation
  auth.py              site / admin passwords and bettor sessions (signed cookies)
  tracker.py           member resolution, 5-stack detection, background polling
  henrik.py            HenrikDev API client
  db.py                SQLite schema and queries (data/tracker.db)
  stats.py             aggregation (overall / per agent / per map / team, stack vs. other games)
  insights.py          datasets for the Visualizations tab (sessions, comps, damage share, ...)
  odds.py              odds engine
  bets.py              betting ledger and settlement
  tunnel.py            Cloudflare Tunnel runner (downloads cloudflared into tools/)
  demo_seed.py         synthetic data for --demo
web/                   index.html, app.js, viz.js (charts), style.css (no build step)
tests/selftest.py      offline test of detection, stats, odds and settlement
data/, tools/          created at runtime (database, cloudflared); not committed
```

Run `python tests/selftest.py` to check the backend end-to-end without touching the API.

## Notes and limits

- The API is unofficial and rate-limited; keep the poller interval reasonable
  and do not point several instances at the same key.
- Stored history only contains matches that HenrikDev has seen for at least one
  player, so very old games may be missing. Everything from the moment the
  tracker starts running is captured.
- Data lives in `data/tracker.db` (SQLite). Delete it to start over; the next
  sync rebuilds the history.
