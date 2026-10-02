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
  Keep it to 4-5 tiles and don't repeat what the card right below shows, or the top bar (the Overview dropped its
  games and win-rate tiles because the brand line already says "21-27 as a 5-stack · 44% win rate").
- **Overview:** fits one 1920×1080 screen without scrolling (about 930px of page under the top bar). `.ov-top` is
  three tiles plus the Next game strip (`.ov-next`, twice as wide), then a 3 × 2 `.ov-grid` of cards whose columns
  are 1.15 : 1 : 0.85 (tables on the left, lists on the right). A new Overview card replaces one of the six rather
  than adding a row; check the page still fits after any change (measure `document.documentElement.scrollHeight`).
  Things that name a game, player or map click through to it (recap, Players, Matches filtered to the map).
- **Two columns:** `.grid-2` for pairs of equal cards. Pages with a sidebar use `.odds-layout` (main column plus a
  320px `aside`): the odds format toggle and bet slip on Place bets (pinned, `.odds-side`; the page has no header row) and Send credits / Game rewards on Standings
  (`.bettors-side`, scrolls with the page because it's taller than the window).
- **Spacing:** 16px between cards and columns, 12px inside groups (tiles, market boxes), 8px between related
  controls. Keep new spacing on that 4px grid.

## Navigation

- The top bar holds 🏠 **Overview** on its own, then four dropdown groups (`.nav-group` in `index.html`):
  📊 **Stats** (Players, Squad, Forecasts, Charts, Matches), 🎲 **Betting** (Place bets, Standings), 🃏 **Casino** (Slots, Blackjack, Poker, Daily wheel) and 🐒 **Onkey's**
  (Shop, Arcade, Monkeys). A new page joins the group it belongs to, as a menu row with an emoji, a name and a
  one-line description; don't add another top-level entry without a good reason.
- A group's button shows the name and emoji of the page you're on (`syncNavGroups()`), so you always know where you
  are without opening it.
- Page names say what you do there: "Place bets" (the odds and your slip), "Standings" (rankings and results). The
  addresses behind them (`#odds`, `#bettors`) are older and stay as they are, so links keep working; when you write
  about a page, use its label, not its address.
- Every nav entry has an emoji in a `.nav-icon` span, but they're off: `NAV_ICONS = false` in `app.js` hides them all
  (it adds `.no-nav-icons` to `<html>`); the markup stays, so setting it to true brings them back.
- The nav follows the brand on the left; its entries are 24px apart with a short vertical line between each, and a
  group's button has a drawn chevron (down, up while open). In a group's menu the page you're on is marked by its background
  alone (no accent bar), and a hairline divides the rows.

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
| `--felt*`, `--card-*`, `--chip*`, `--turn` | green felt, white cards, gold chips | The casino tables. Defined once on `:root` and not redefined per theme: the felt and the cards look the same in every theme, like the slots cabinet |

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
| Top-bar chips | `renderMe()` → `#me-credits`, `#me-bananas`, `#me-chip` | Signed out, only the Sign in chip shows. The profile chip opens the account menu (`#account-menu`: the sign-in form, whose optional Riot ID field puts a new account on the squad, or profile / password / sign out plus your squad entry with Sit out / Join, Change Riot ID and Leave the squad); the credits chip is the one place the balance shows, and it ticks and throws the win confetti. Anything with `data-signin` opens the menu |
| Better / worse than expected | `.fc-bar-row` (Forecasts, `fcGridCard()`) | A bar either side of a zero line, better to the right, paler with fewer games; rows sorted best to worst and clickable to filter. Use this, not a shaded grid, for "how far off expected" per group |
| A forecast on a number line | `.fc-next-line` (Forecasts, `fcNextCard()`) | Range band, typical-game tick, dashed betting line, end labels |
| A market group as questions | `.tm-match`, `.tm-form`, `.tm-qgrid` / `.tm-q`, `.tm-mountain` (Place bets, team markets) | The main bet first and bigger, its bar centred and split by chance, recent form as small W / L chips under the middle, a nudge below the chances either side, and the two buttons centred vertically on the bar and that line; every other market a card asked as a plain question with its picks (the fact line under each is off, `TM_FACTS = false` in `app.js`, to keep the section short); a many-pick market (final score) as one row of columns whose height is the chance, the likeliest outlined |
| Cards of different heights | `.bettor-slips` (CSS columns) | Stacks cards without holes; `break-inside: avoid` on each card |
| One list per player | `.ap-grid` > `.ap-col` (Charts, agent pool) | A column per player listing only what they have, instead of a sparse player × item grid |
| Line-up tables | `table.roster`, `tbody.drop-target[data-zone]`, `.grip`, `tr.slot-empty`, `tr.me` (Squad, `viewSquad()`) | Two tables with the same columns, Active squad above Bench. Rows drag between them (a ⋮⋮ grip in the first cell, the target tbody outlined with a dashed accent while a row is over it) and every row also has a Bench / Swap in button, so the page works without a mouse. The squad's open spots are dashed `slot-empty` rows that say what to do; a button that can't apply (full squad, minimum size) stays visible but disabled with a `title` saying why. Your own row is tinted like your Standings row. A line-up change posts the whole order, so dropping a row also reorders the colours |
| The house | `houseCard()` → `.house-card` (Standings sidebar, first), `.house-pots`, `.house-goals` (✓ met / ✗ missed / – no result), `.house-recent`; recap `house()` (`.status` pills) | The pot and jackpot as two small tiles, the next game's objectives as a count and prizes only (they're secret), the last game's objectives revealed with who got what (`plainName()`), the latest giveaways. Rules are folded in a `how()` |
| Odds boost | `.boost-card` (Place bets sidebar, under the odds format toggle), `.odd.boosted` | A sidebar card: the pick, then the usual price struck through beside its `oddBtn()`, then the rule. Wherever the boosted pick shows on the board, its button gets a static gold ring (`--warn`); a hot or cold streak's border wins over it. Added to a parlay, that leg carries the same ⚡ mark and a `--warn` accent stripe in the slip (`.parlay-leg.boosted`) and on its ticket (`.bet-leg.boosted`), and `parlayQuoteHtml()` notes the cap |
| Onkey talks | `web/onkey.js` → `.onkey-says` under the top-left logo, `.brand.onkey-talking` / `.onkey-excited` | The casino dealer's voice site-wide: his noises in italics over a bold line in a card-coloured bubble, tail up to the logo, which hops and jabbers while he talks (three hops when excited). Idle lines come every 70-140 s while the tab is visible, reactions straight away after something you did; at the blackjack and poker tables he walks from the logo to the dealer's seat (the logo stays empty and the dealer's face waits for him; `.onkey-walker`, a waddle with a bob and tilt per step) and walks back when you leave, and he's silent there; idle lines make no sound, and clicking the bubble hushes him for 15 minutes. `aria-live` is off: it's chatter, not news. New lines go in `IDLE`, `SAY` or `DYNAMIC`; pages report events with `FiveOnkey.note(kind, detail)` |
| A result still turning | `holdBalance()` / `releaseBalance()` / `displayBalance()` (app.js) | A spin the server has already settled (slots, the daily wheel) holds the balance shown in the top bar and on the machine (less the stake for slots) until its animation lands, so a background refresh can't give the result away; releasing it counts the chip up to the real balance. Any new game whose result is known before its animation ends should do the same |
| Wheel tokens in the bet slip | `tokenRow()` → `.slip-tokens` > `.token-btn` (`.on`), `.token-price`, `perkNote()` → `.bet-perk` on tickets (bets.js) | Shown on each single only while you hold a token: a pill per kind with the count; one token per pick, each token on one pick at a time, a boost greyed out on the game-boosted pick. Turned on, the pick shows its boosted price in gold and its to-win says so; the ticket names the token |
| A waiting reminder | `#me-wheel` (top bar), `.nav-alert` (a nav trigger or menu entry) | Something the signed-in bettor can do now that they'd miss otherwise (today: their daily wheel spin, `wheelReady()`): a gold chip in the top bar that goes there, hidden on that page, and a small pulsing gold dot on its nav group and menu entry. Both go away once it's done. Reduced motion keeps the dot still |
| Prize wheel | `web/wheel.js` → `.wheel-layout`, `.wheel-row`, `.wheel-box` (SVG `#wheel-rotor`, `.wheel-slice.wk-<kind>`, `.wheel-pointer` hanging from `.wheel-pivot`, `.wheel-center`), `.wheel-prizes` | The Daily wheel page: the wheel is the one big thing (880px, with the result and Spin button in a column beside it so everything fits a 1080p screen), a slice's angle is its real chance (so the jackpot is a thin gold sliver with a halo and a bright centre line; no marker over it), the jackpot amount in the hub, and the prize table (rarest first), tokens and spin lists beside and below. Slice colours are `--wheel-*` tokens, as are the medium grey rim (`--wheel-rim`, its bulbs on its outer edge, warm white, red and blue in turn (`--bulb-1`…`--bulb-3`), none behind the flapper) and the black flapper hanging from a small silver pin (`--wheel-flapper`). The spin is a physics simulation played back frame by frame (momentum, air drag, rolling friction, and pegs that must push a sprung flapper aside), solved so it comes to rest in the server's slice: it never jerks, and a tease is just the wheel reaching the deciding peg with barely enough momentum to creep over it, or barely too little so the flapper pushes it back (the stage darkens, bulbs pulse gold, a drone and heartbeat play while it crawls), on wins and misses alike. The pointer is that flapper, short enough that its tip only just reaches the pegs, which straddle the wheel's edge, half on the slices and half on the rim (it never passes through them): pegs bend it and it springs back with a damped wobble. Each prize has its own sound and screen effect (`celebrate()`), the jackpot all of them: dim, triple flash, shake, banner, gold rain. At rest the bulbs run a slow chase and the jackpot's halo shimmers, the only idle animation besides the slots cabinet. The angle and the flapper's bend are kept across redraws; reduced motion skips the turn, flashes, shakes and confetti and keeps the sound and the result. Mute is `fs.wheelMuted` |
| Inline add form | `.roster-add` (Squad) | One row: labelled inputs (Riot ID, nickname) ending in a button, with the explanation in a `.muted.small` line under it. The admin form sits folded in a `details.how` so the page stays about the tables |

### Slots

`#slots` lives under Casino, labelled Slots (`#casino` opens it too). One machine, no names or picker, and
no page title or links above it: the cabinet starts the page.
`.slots-layout` pairs a wide arcade cabinet with a 300px pay table, exactly as tall
as the cabinet (`contain: size`, prize rows sharing the height; an opened "How this
works" scrolls inside it). Like the
Arcade, the cabinet stays dark in every theme: `--slot-*` tokens define plum
housing, gold controls and ivory reels. Surrounding cards follow the theme.
The cabinet is drawn as a physical machine; it's the page's one bold element, and
everything around it stays quiet. An LED frame runs round the whole machine
(`.slots-leds`: 206 lights at fixed spots on a rounded outline in the cabinet's
16px edge, built once as `LEDS` in `slots.js`, clockwise from the top left, evenly
spaced round the corners too). The lights never move; a
chase runs round them, two thirds of each run of nine lit at a time, so long lit
runs with short dark gaps (`--k` staggers each light's `animation-delay`): one light
every 0.18 s at rest, every 0.06 s while the reels spin. A win stops the chase and
every light flashes together for about three seconds; then all stay lit and each
rejoins the chase at the start of its own lit run (`slots-led-again`, a fresh
animation), so the gaps grow back in rather than snapping on. Bigger wins bring
more colour into the frame until the next spin (`--led` per light, set by the
`.slots-tier-*` classes): gold alone up to a spike, then a diamond mixes in ice
blue, a banana magenta, Onkey green, and the Golden Onkey makes it a six-colour
rainbow. The body sits inside the frame
(`.slots-body`). A monospace "Slots"
marquee with a round speaker toggle (`speaker()`, crossed out when muted), a reel
window in a chrome bezel with a curved glass glare (`.slots-glass::after`), a pull
lever inside the machine on its right (`lever()`, `.slots-lever`, midway between
the reel window's chrome and the LEDs; the stage has the same 108px of extra room
on the left, so the reels sit centred under the marquee: a bolted chrome plate with a slot,
a tapered rod on a pivot hub and a red ball; pulling it spins, and on every spin
the rod folds down through the pivot while the ball swings toward you, drops below
and springs back), and the deck: one control panel (`.slots-deck`, lit from
above, a chrome trim with screws at its corners and a lip in front) with three
recessed wells (`.slots-well`) sharing its amber light: the bet keys (backlit push
buttons reading "Bet 5", dim amber at rest and lit when chosen), the machine's
readout (`readout()`: Credits, Bet and Win in amber digits) and the Spin button, a
red domed arcade button reading just "Spin" (its label says the stake), with a
glossy highlight in a stepped chrome bezel with an amber glow, which sinks a
little when pressed. Chrome is `--slot-chrome` / `--slot-chrome-dark`. There's no footer. The gold centre line marks the paying symbols;
adjacent symbols remain partly visible above and below. Seven stake buttons show
the selection. Payouts list largest first, with multipliers and current credits,
each line's chance ("1 in 20") and how often you've hit it, with a note on when
your hits started counting. A `secret` symbol (the Golden Onkey) never appears in the pay
table or on the strips (below). Onkey is a picture (`.slots-img`), sized to sit
with the emoji symbols on the reels, in the pay table and in recent spins; the
Golden Onkey is the same picture turned gold with a golden glow (`.golden`, a CSS
filter, also on its banner and rain).
Balance stays in the top bar. Below the machine, three cards side by side
(`.slots-lower`, 1.3 : 1 : 1, equal heights): your recent spins (no machine column),
Your season (`season()`: tiles for spins, net, wins against the expected rate,
spins since your last win, and your biggest win with its reels) and the squad's
Biggest wins this season (`bigWins()`, top five, bettors as `plainName()`).
Standings has a separate Casino column (every casino game, slots included).

Reels use continuous symbol strips moved with `translate3d` in requestAnimationFrame.
Each strip matches the machine's display weights (`show`, `stripsNow()`): a
symbol gets a cell per share (10 cherries, 4 Onkeys in 42), spread evenly with no symbol
next to itself, laid out differently on each reel, so rarer symbols pass by less.
Secret symbols have no cells: when a reel stops on the Golden Onkey, it takes over
the landing cell (`swapped`, `setCell()`) just before it rolls into view, and gives
it back once it has rolled away on the next spin, so it's only seen where it lands
(about 1 spin in 150).
They accelerate, then decelerate with matching incoming velocity and stop left
to right on the server's result. Repaints retain their positions; leaving the tab
cancels motion. Reduced motion skips rolling (and the tease).

The tease: when the first two reels match, the third may keep spinning after the
others stop, pulse gold (`.slots-reel.teasing`, the other two dimmed) over a
rising drone, then slow to a crawl and creep the last cells with a tick per
symbol, so you can't tell whether it will land. Whether it happens comes from the
first two reels only (`TEASE`: never for a cherry, bell or spike pair, 55% for diamonds up to 90% for Onkey and
always for the Golden Onkey), never from the result, so a tease gives nothing away on
wins or losses; `TEASE_LEVEL` makes it longer and slower for bigger pairs (about
2.7 s for cherries to 5.5 s for Onkey), plus the ending (below).

The stops slow down for bigger symbols (`PAUSE`): the wait before the second reel
grows with the first reel's symbol (none for a cherry, 0.65 s for Onkey), and
before the third with the second's, longer still after a matching pair; a tease
replaces the third. They follow what's already on screen, so they come on losses
too. The opening spin runs about a second longer on every banana, Onkey or Golden
Onkey win and on one spin in ten besides (`BIG_OPENING`), so a long spin is no
giveaway.

The tease's ending: the creep stops half on
the pair's symbol and half on its neighbour, teeters there for a second or so to a
heartbeat while the payline flickers red (`.slots-glass.teetering`), then snaps a
half cell with a little overshoot, a thump and a nudge of the cabinet. On a win it
snaps onto the match from either side; on a loss the third reel lands on a cell of
its real result next to the pair's symbol where the strip has one, and snaps back
when the match was just short (below) or forward when it had crept just past
(above): a deliberate near miss. It only picks between identical-looking cells,
never changes a result or the odds. The reel then flashes gold with a crash and a
rising sting, or red with a sad trombone (`.tease-won` / `.tease-lost`).
Wins light the cabinet and the matching pay-table row. The marquee glows while
the reels spin (`.slots-busy`). Synthesized sounds (`sound()`: tones and filtered
noise) mark the spin (a lever and a whoosh), a ratchet clank as each symbol passes
the line while the reels turn (one stream for the machine, at most every 70 ms
across the reels), each stop (a thunk),
the tease (a
drone, ticks, heartbeats, the snap and its outcome) and a win (cymbals and a bass
note on the bigger ones), with a persistent speaker toggle. Four
symbols get celebrations that build on each other (`FX` and `celebrate()` in
`slots.js`, `.slots-tier-1`…`4` for the glow): diamond adds an emoji confetti
burst; banana a banana burst, a light shake and falling bananas; Onkey a gold
flash, a harder shake, gold confetti, falling Onkeys and an "ONKEY!" banner; the
Golden Onkey dims the page, flashes three times, shakes hardest, fires five
confetti bursts, rains golden Onkeys and shows a "GOLDEN ONKEY!!!" banner. Each fanfare is longer than the last.
The moving parts live in a fixed `.slots-fx-layer` that removes itself, so they
play once and never replay on a redraw. Reduced motion keeps the glow, the sound
and the banner (faded, not scaled) and drops the shake, flashes, rain and
confetti. Idle animation is welcome on the cabinet, which should feel like a lit machine
on a casino floor: the LED chase runs at rest. Everything else on the page stays
still until it's used, and reduced motion stops all of it. Results use a live status region and errors appear inline. An uncertain
request locks stakes and offers "Check last spin"; "Sign in to spin" uses the
existing account menu.

### Onkey's Shop: the casino section

The shop's catalogue comes in groups (`groups` from `/api/shop`): Your looks, then Onkey's Casino, which opens with a
`.shop-group-head` card (a felt-coloured disc and a `how()` line) before its slots. The collection map shows a block
per group (`.cm-group`), its label a filter for the whole group (`state.shopSlot` `g:casino`). Casino items are drawn
on a small felt (`.shop-felt`) on their cards, and their preview is a before / after of your seat as the table sees
it (`casinoSample()`: plate, face-down cards, a bet and a stake chip), Onkey announcing you (`entranceSample()`), or a
Play button for a Table win.

### Blackjack and poker

`#blackjack` and `#poker` live under Casino. Both are a `.casino-page`: a `.casino-bar` (the page's own controls, the
mute button on the right), then `.casino-layout`, the table (main column) plus a 340px `.casino-side` of cards.
The table is the page's personality, like the slots cabinet; the side cards are ordinary site cards.

- **The felt** (`.felt`): green, a wooden rail (`--felt-edge`), light text (`--felt-ink`, `--felt-dim` for muted).
  Blackjack's is a rounded rectangle with Onkey and his hand at the top, the table print ("Blackjack pays 3 to 2"),
  the player spots, then the status line and controls inside the felt. Poker's is an oval (`.pk-rail`) with eight
  seats placed round it by percentage (`SPOTS` in `poker.js`), turned so your own seat is at the bottom; the board and
  pot sit in the middle, each seat's bet as a chip part-way to the middle, and the status line and action bar sit
  under the oval.
- **Cards** (`FiveCasino.card()`, `.pcard`): drawn in CSS, no images: rank and suit top left, a big pip bottom right,
  red for hearts and diamonds; sizes `xs` (logs and tables), `sm` (other players), `md` (your poker hand), `lg`
  (blackjack and the board). A face-down card is `.back`, an empty board slot `.empty`. Every card has an
  `aria-label` ("Ace of spades").
- **Dealing** (`FiveCasino.deal()`): moves are settled on the server at once but played out on the page. Every
  table card has a `key` (table, round or hand, seat, position) and a `seq` (its place in the real deal order:
  two rounds round the table, then hits and splits, Onkey's hole card turning over, then his draws; at poker the
  hole cards from the left of the button, a showdown's flips, then the board). New cards fly in from Onkey one at a
  time (`.deal-in`, 380 ms apart) and a face-down card turns over (`.flip-in`); a card re-rendered mid-flight keeps
  its place (a negative delay). Totals, results, hand names, the status line and the controls carry `.after-deal`
  until the last card lands (`FiveCasino.after()`), and Onkey's line, sounds, Table win bursts and the credits chip
  wait for it too. Opening a table shows what's on it without dealing it again. Reduced motion skips all of it.
- **Onkey, the dealer** (`FiveCasino.dealer()` / `say()`): his picture with a white speech bubble to its right. When
  he speaks, the bubble's first line is his monkey noises ("Ook Eek Ook", with "!" when he's excited) and the second
  what they mean, while Web Audio plays matching ooks (low, falling) and eeks (high, rising); his picture jabbers
  (off with reduced motion). He reacts to the table's log, one line per update, the most interesting event first
  (`PRIORITY`); his lines are `QUIPS`, casual and short. One mute button (`fs.casinoMuted`) covers both tables.
- **Turns**: the player to act gets a gold outline (`--turn`) and a shrinking `.timer-bar`; countdowns
  (`[data-deadline]`) tick every 250 ms against the server's clock and go gold, then red, under 6 seconds.
- **Controls**: stakes are round gold chips (`.stake-key`); moves are big `.casino-act` buttons with the amount as a
  second line (Hit / Stand / Double / Split; Fold / Check or Call / Raise to), the primary move (Hit, Raise) in the
  accent colour and Fold in red text. Poker's raise has a slider, a number field and presets (Min, ½ pot, Pot, All
  in). Keyboard shortcuts: H S D P and Enter at blackjack, F C R at poker.
- **Live updates** (`FiveCasino.live()`): the shared tables long-poll, and `patch()` replaces only the regions whose
  HTML changed, keeping keyboard focus and a text field's caret, so a rules form or raise amount survives other
  players' moves. Results, errors and turns go through a `role="status"` line.
- **What players bought** (Onkey's Shop, `FiveCasino.style()` / `who()`): names at the tables use `nameHtml()`
  (name colour, badge, pranks, and the title on the seat), and each player's casino items style their own things:
  card backs (`.cbk-*` on their face-down cards), chips (`.chp-*`, which set `--chip-bg` / `--chip-fg` on
  `.chip-stake`, `.chip-dot` and your `.stake-key`s), seat style (`.st-*` on `.pk-seat` plates and `.bj-spot`s; the
  turn outline always wins), Entrance (Onkey's line when they sit down) and Table win (a burst from their seat,
  found by `data-bettor`). Table answers carry `looks`, merged into `state.looks`, so a purchase shows at the next
  table update.
- **Side cards**: Blackjack shows Your season (four tiles), the shared table's players and House rules. Poker shows
  Your seat (buy-in, or chips, Ready up / Not ready, leave, top up), Table rules (an editable form for seated players
  in the lobby, else a list), the last hand (board, each player's cards, hand and net), Your season, and Table talk
  (the log in words, newest first). Bettors appear as `plainName()`.

## Words

- Name things by what people see and do, not how they're built: "Send credits", not "create transfer".
- Buttons say what happens ("Place parlay", "Send", "End season"), and the toast afterwards uses the same verb.
- Names in small text: wherever a bettor's name sits in small or secondary text (tile sub-lines, meta lines, notes,
  anything around 12px), show the simplified name, `plainName(name)` in `app.js`: their colour swatch and the plain
  name. The shop's full look (`FiveShop.nameHtml()`: name colour, badge, title, prank marks) is for names at full
  size, such as ranking rows, ticket headers and profile cards, where the decorations have room and are the point.
- Empty states say what to do next ("Sign in to spend bananas", with Sign in opening the account menu via `data-signin`).
- Each box does one job: the bet slip holds picks only (no sign-in form, no balance).
- Errors say what went wrong and how to fix it, without apologising.
- Meta lines may join short facts with " · " (`Abyss · Sep 26 5:00 PM · recap`); don't use it in headings or labels.
- Casual is fine (it's a friends' site: "the generous monkey gets rewarded"), but numbers and rules stay exact.
- The squad's size is a setting, not a fact: write `${stackWord()}` (`web/common.js`, from `FiveRoster.size`) where a
  sentence names it ("5-stack", "3-stack", or "squad" before the roster has loaded), never a literal "5-stack". The
  site's name and the nav keep "5-Stack"; page copy follows the roster. "Squad" is the active players, "bench" the rest
  of the pool, and "pool" everyone the tracker knows; buttons use those words ("Bench", "Swap in", "Remove me").

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
- The Casino tables (2026-10-01) were checked in dark and light at 1920×1080 with headless Chromium and three
  signed-in players (solo and shared blackjack, a full poker hand to showdown); a real mouse on the raise slider, the
  sounds, and the shop themes are still to be checked.
- Not yet reviewed: the signed-in top bar and bet slip (the demo has no account to sign in with), and the shop themes
  after the width change. Light mode has been checked on Forecasts and Charts.
- The Squad page (2026-09-30) was checked in dark at 1920×1080 with a headless browser against the fake API, and its
  drag and drop only through the buttons that do the same thing; a real mouse drag, touch, and light mode are still to
  be checked. The demo squad is fixed, so the editable page needs a real API key (or the self-test's fake client).
