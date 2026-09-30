# Design guide

How the 5-Stack Tracker looks and behaves, for anyone (people or AI agents) changing the UI in `web/`. It records
the decisions already made so new work matches them. It's a working document: when you settle a new pattern or
retire an old one, update this file in the same pull request.

`docs/DATA.md` covers the data and endpoints; this file covers what the page does with them.

## Ground rules

- **One screen: 1920×1080 desktop.** The site is built and checked at that size only (the squad plays on PCs,
  where it's the most common screen). Don't spend effort on phone, tablet or small-laptop layouts. Old
  `@media (max-width: …)` rules still in `style.css` can stay but aren't maintained.
- **Dark by default.** The page opens in dark whatever the OS prefers; there is no `prefers-color-scheme` handling.
  Light and the shop themes apply only when picked with ◐ (`data-theme` on `<html>`, saved in `fs.theme`). Design
  and review in dark first, then check light.
- **No dependencies, no build step.** Plain HTML, CSS and JS; system fonts. No web fonts, icon fonts or libraries.
- **Stats first, fun second.** Most of the site is numbers people compare. The Arcade, the Shop and bought
  cosmetics are where the personality lives; the data pages stay calm and consistent around them.

## Layout

- **Page width:** every tab uses `.container`, max 1600px, centred, 16px padding. One width for all tabs, so the page
  doesn't jump as you switch (there used to be a narrower 1240px default; don't bring it back).
- **Cards:** content sits in `.card` (surface colour, 1px border, 12px radius, 16/18px padding, 16px apart). A card
  opens with an `h2`; if it links elsewhere, the heading row is a `.section-head` with a `.go-link` on the right.
- **Summary row:** `.kpis` is a row of `.tile`s (label 12px muted, value 28px, sub-line 12px) at the top of a tab.
  Keep it to 4-5 tiles and don't repeat what the card right below shows.
- **Two columns:** `.grid-2` for pairs of equal cards. Pages with a sidebar use `.odds-layout` (main column plus a
  320px `aside`): the bet slip on Place bets (pinned, `.odds-side`) and Send credits / Game rewards on Standings
  (`.bettors-side`, scrolls with the page because it's taller than the window).
- **Spacing:** 16px between cards and columns, 12px inside groups (tiles, market boxes), 8px between related
  controls. Keep new spacing on that 4px grid.

## Navigation

- The top bar holds 🏠 **Overview** on its own, then three dropdown groups (`.nav-group` in `index.html`):
  📊 **Stats** (Players, Forecasts, Charts, Matches), 🎲 **Betting** (Place bets, Standings) and 🐒 **Onkey's**
  (Shop, Arcade, Monkeys). A new page joins the group it belongs to, as a menu row with an emoji, a name and a
  one-line description; don't add another top-level entry without a good reason.
- A group's button shows the name and emoji of the page you're on (`syncNavGroups()`), so you always know where you
  are without opening it.
- Page names say what you do there: "Place bets" (the odds and your slip), "Standings" (rankings and results). The
  addresses behind them (`#odds`, `#bettors`) are older and stay as they are, so links keep working; when you write
  about a page, use its label, not its address.
- Every nav entry has an emoji in a `.nav-icon` span. `NAV_ICONS = false` in `app.js` hides them all (it adds
  `.no-nav-icons` to `<html>`); the markup stays, so turning them back on is one change.

## Colour

All colours are CSS variables on `:root` in `style.css`, redefined for `light` / `greg` and each shop theme. Never
hard-code a colour in a component; add or reuse a token so every theme picks it up.

| Token | Dark value | Use |
|---|---|---|
| `--bg` | `#0d0d0d` | Page background |
| `--surface` / `--surface-2` | `#1a1a19` / `#232322` | Cards / insets, tracks, hover fills |
| `--border` / `--grid` | 10% white / `#2c2c2a` | Card borders / table rules and chart gridlines |
| `--text` / `--text-2` / `--muted` | `#fff` / `#c3c2b7` / `#898781` | Main text / secondary / labels and footnotes |
| `--accent` / `--accent-soft` | `#3987e5` / 16% | Links, selected states, primary buttons / selected backgrounds |
| `--up` / `--down` | `#0ca30c` / `#e66767` | Better and worse (wins, profit, trends) |
| `--good` / `--warn` / `--bad` | green / amber / red | Status: connected, demo banner, errors and danger buttons |
| `--s1` … `--s8` | blue, orange, teal, gold, pink, … | One per squad member slot (`bettorSlot()`); the same person is always the same colour |
| `--axis`, `--div-*`, `--aim-*`, `--pair-*`, `--spark`, `--bar-*` | | Charts (see Charts) |

- Green and red mean better and worse only. Don't use them for decoration.
- Player colours come from the slot, never picked per chart.
- Greg Mode makes `--surface` / `--surface-2` 50% transparent, so anything drawn on a surface must stay readable over
  the Greg background. Tooltips and toasts stay solid.

## Type

- System UI font (`--font`). Body 15px, line height 1.45. `h1` and `h2` 17px (h2 weight 600), `h3` 14px weight 600 in
  `--text-2`. Labels 12-13px in `--muted`. Big numbers: tile values 28px, weight 600.
- Numbers that line up in columns use `font-variant-numeric: tabular-nums` (`.num` does this and right-aligns).
- **Sentence case everywhere.** No all-caps labels or headings. The only all-caps text is deliberate styling: the
  Arcade's pixel signage, the "PARLAY" ticket badge, the "Placed" stamp, the "WANTED" bounty prank and bought titles
  (`.btitle`), which are the item's look.
- Keep prose lines readable: a paragraph shouldn't run the full 1600px (Setup's longest notes do today).

## Components

Reuse these before inventing new ones.

| Component | Class / helper | Notes |
|---|---|---|
| Card | `.card`, `.section-head` | `h2` title, optional one-line intro in `.muted`, then content |
| Summary tiles | `.kpis` > `.tile` (`.tile-label`, `.tile-value`, `.tile-sub`) | `.tile-link` when the whole tile opens something |
| Explanations | `how(summary, more)` → `<details class="how">` | One line visible, "How this works ▸" for the rest. Defined in app.js, bets.js, viz.js and recap.js |
| Segmented picker | `.seg` > `.seg-btn` (`.on`) | Picking a stat, player, genre or mode. Put a `.swatch` in player buttons |
| Buttons | `.btn`, `.btn.ghost`, `.btn.small`, `.btn.icon`, `.btn.primary` (full width), `.btn.danger` | Actions only; links that go somewhere are `.go-link` |
| Page links | `.go-link`, grouped in `.head-links` | Accent colour, no underline until hover, text ends in " ›" |
| Result chip | `.chip.win` / `.loss` / `.draw` | W / L next to a score |
| Bet status | `.status.won` / `.lost` / `.pending` / `.void` | Pills on tickets |
| Player dot | `.swatch.s1`…`.s8` (`.lg` for headers) | Before every player name |
| Bettor names | `FiveShop.nameHtml(name)` | Always, so bought colours, badges, titles and pranks show everywhere |
| Odds button | `oddBtn()` → `.odd` (`.on`, `.hot`, `.cold`) | Every price that can be added to the slip |
| Bet tickets | `betTicket()`, `bettorSlips()` → `.bet-slip-card` | Grouped by bettor; `ticketClass(name)` adds bought ticket styles |
| Tables | `table`, `.table-wrap`, `th.num` / `td.num` | Row names are `<th scope="row">` (full-colour, weight 600); headers are muted 12px |
| Tooltips | `data-tip` JSON, drawn by `FiveViz.mount` (`.viz-tip`) | Rendered with `textContent`; call `FiveViz.mount(view)` on any tab that uses them |
| Toasts | `toast(msg, 'good' \| 'bad')` | Short, past tense for what just happened ("Sent 50 credits to Matt") |
| Top-bar chips | `renderMe()` → `#me-credits`, `#me-bananas`, `#me-chip` | Signed out, only the Sign in chip shows |
| Better / worse than expected | `.fc-bar-row` (Forecasts, `fcGridCard()`) | A bar either side of a zero line, better to the right, paler with fewer games; rows sorted best to worst and clickable to filter. Use this, not a shaded grid, for "how far off expected" per group |
| A forecast on a number line | `.fc-next-line` (Forecasts, `fcNextCard()`) | Range band, typical-game tick, dashed betting line, end labels |
| Cards of different heights | `.bettor-slips` (CSS columns) | Stacks cards without holes; `break-inside: avoid` on each card |
| One list per player | `.ap-grid` > `.ap-col` (Charts, agent pool) | A column per player listing only what they have, instead of a sparse player × item grid |

## Words

- Name things by what people see and do, not how they're built: "Send credits", not "create transfer".
- Buttons say what happens ("Place parlay", "Send", "End season"), and the toast afterwards uses the same verb.
- Empty states say what to do next ("Sign in on the Place bets page to spend bananas").
- Errors say what went wrong and how to fix it, without apologising.
- Meta lines may join short facts with " · " (`Abyss · Sep 26 5:00 PM · recap`); don't use it in headings or labels.
- Casual is fine (it's a friends' site: "the generous monkey gets rewarded"), but numbers and rules stay exact.

## Charts

- Hand-built SVG/HTML in `viz.js`: `html()` leaves empty `[data-chart]` slots, `mount()` draws them at their real
  width and redraws on resize.
- Colours come from the chart tokens (`--axis`, `--div-pos` / `--div-neg` / `--div-mid` for diverging scales, `--s*`
  for players), defined in every theme block, so switching theme needs no redraw.
- Every chart gets a one-line takeaway under its title ("Matt leads at +358 credits") and hover tooltips with the
  exact numbers.
- Use the `dataviz` skill's guidance for new chart types, palettes and tiles.

## Motion

- Motion answers something the person did or something that just happened: the balance ticking up, confetti for a
  win, the "Placed" stamp, hot and cold odds borders. No entrance animations on sections or hover effects on every
  card.
- Respect `prefers-reduced-motion` (Greg drops and confetti already do).

## Accessibility

- Visible keyboard focus on every control (`:focus-visible` outlines; links get one from the base `a` rule).
- Icon-only buttons have `aria-label`; decorative emoji are `aria-hidden="true"`.
- Pickers use `role="tablist"` / `aria-selected`, menus `role="menu"`, as the existing ones do.
- Text on surfaces keeps readable contrast in every theme; check new colours in light and Greg Mode too.

## Checking a UI change

1. Run `python server.py --demo`.
2. Open each changed tab at a 1920×1080 window in dark, then in light. With the Playwright MCP, set the viewport to
   1920×960 and take full-page screenshots into `.playwright-mcp/` (ignored locally through `.git/info/exclude`).
3. Moving between `#tabs` doesn't reload the page, so reload (or add a `?` query) after editing CSS or JS.
   A browser profile with a saved `fs.theme` stays on that theme; clear it to see the default.
4. No console errors, nothing wider than the window.
5. Update this file if the change adds or retires a pattern.

## Known issues and backlog

The 2026-09-30 review at 1920×1080 fixed: page widths, the signed-out top bar, nav icons, all-caps labels, link
styles, dimmed names in tables, Overview's duplicate "Last game", bet-slip card gaps, the Shop's exchange-rate card,
the Charts agent pool, long prose lines, and the Forecasts page (next-game card, tiles following the filter, bars per
map, a readable chart that opens recaps). Still open:

- **Forecasts ideas:** a "lately" read (beat the forecast in 7 of the last 10) with a running-difference line; a
  by-agent breakdown (the data is in `overall.agents`); a calibration row for all five players (costs a replay per
  player on the server); one stat picker plus a per game / per round switch instead of three groups; merging the
  better and worse tiles into one with an above / inside / below bar; a last 20 / 50 / all toggle on the chart.
- Not yet reviewed: the signed-in top bar and bet slip (the demo has no account to sign in with), and the shop themes
  after the width change. Light mode has been checked on Forecasts and Charts.
