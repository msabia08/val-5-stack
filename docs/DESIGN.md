# Design guide

How the 5-Stack Tracker looks and behaves, for anyone (people or AI agents) changing the UI in `web/`. It records
the decisions already made so new work matches them. It's a working document: when you settle a new pattern or
retire an old one, update this file in the same pull request.

`docs/DATA.md` covers the data and endpoints; this file covers what the page does with them.

## Ground rules

- **Two screens: a 1920×1080 desktop and a phone.** The desktop is the main one (the squad plays on PCs, where
  it's the most common screen); the phone layout (see Phone below) is being added page by page, the Overview and
  the top bar first. Don't spend effort on tablet or small-laptop widths. Other old `@media (max-width: …)` rules
  still in `style.css` can stay but aren't maintained.
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
  Keep it to 4-5 tiles and don't repeat what the card right below shows. The top bar's brand is the logo and the
  title alone; the squad's record and win rate are the Overview's Record tile.
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

## Phone

Everything for phones is in one block at the end of `style.css`, `@media (max-width: 640px)`, checked at 393 × 659
(an iPhone 15 with Safari's bars showing). It rearranges what the desktop page already draws: no separate markup, and the only
JS is the menu button's, the bet slip's handle and the Banana Hunt's field. So far it covers the top bar, the Overview, Place bets, the Banana Hunt, Slots, Blackjack and the Daily wheel; other pages still show their desktop layout.

- **No sideways scrolling.** `document.documentElement.scrollWidth` equals the screen width. A phone zooms the
  whole page out when anything is wider, which shrinks all the text. Grid columns are `minmax(0, 1fr)` so content
  can't push them wider.
- **Top bar:** one pinned row, 51px: Onkey's logo (no title), the 🎡 while a daily spin is waiting, credits,
  bananas, your avatar (it opens the account menu; signed out it says "Sign in") and ☰. `.topbar-right` is
  `display: contents`, so its children are items of the top bar itself.
- **The menu:** ☰ (`#nav-toggle`, hidden on the desktop) opens the nav as a full-screen menu under the bar:
  `navSheet()` in `app.js` puts `.nav-open` on `<html>`, and the phone block draws `#tabs` as a fixed panel with
  every page three across under its group's name (names only, the page you're on outlined). Sync now, Setup and
  Theme (and Log out, on a site with a password) are hidden in the bar and pinned along the menu's bottom edge.
  A pick, a page change, the account menu or Escape closes it. A new page needs nothing extra: it's the same
  markup as the desktop dropdowns.
- **Onkey's bubble** opens to the right of the logo, over the bar's chips, and reaches at most about 30px below
  the bar, so it doesn't cover the top of the page (the dealer's cards at the tables). Taps pass through it to the
  chips and ☰, so it can't be tapped to hush him. His lines stay up half as long (`PHONE_BUBBLE` in `onkey.js`). He never leaves the logo on a phone: at the casino tables there's no dealer on the felt (`walk()`
  does nothing), and the dealer's lines come from the logo's bubble (`say()` in `casino.js` hands them to
  `FiveOnkey.speak()` with `table: true`). So no Greg in the dealer's chair either.
- **Overview:** Record and Last session side by side, Form and Next game full width under them, then the six cards
  one per row in their desktop order. Who's trending drops its sparkline column. It scrolls; the
  one-screen rule is for the desktop.
- **Place bets:** one column: the odds format, the boost, team markets, player props, the custom line, the
  scoreboard cards, your open bets, then everyone's.
  - *Team markets:* Win and Loss side by side with the chances and the last five results under them; the questions
    two to a row ("By how much?" full width, three answers to a row); the final score as a six-across grid of
    buttons in score order (no bars), losses red, overtime amber, wins green.
  - *Player props:* no table. Each player is a block and each stat a row: its name (the cell's `data-label`), the
    line, then over and under as two buttons at the right.
  - *Bet slip:* a bar pinned to the bottom of the screen once it holds a pick, showing the count; tapping it
    opens the slip as a sheet (up to 72% of the screen) with the stakes and the Place button, and tapping again
    closes it. `html.slip-open`, toggled from the slip's heading in `bets.js`; an empty slip shows no bar.
- **Banana Hunt:** the field comes first, upright and about a screen tall, with no title or description over it;
  then the tiles two to a row and the top pickers. The server's field is 1200 × 600, so `layout()` in `hunt.js`
  turns it on its side (the server's x runs down the screen, its y across) and scales each axis to fit. A tap
  within `TAP_R` (30px, a fingertip) of the banana as drawn counts as picking it and is sent as the banana's
  own spot, as a keyboard pick is; any other tap is sent where it fell, a miss. The banana is 46px. The same goes for a
  bunch's bananas, the rotten decoy, Greg and a banana in the air (a tap within `TAP_R` + 10 of it as drawn is the catch).
- **Slots:** the cabinet is the screen's width with no lever, no Spin button and no result line under the reels:
  tapping the reel window spins (`slots.js` clicks the hidden Spin button, so a spin under way, too few credits
  and signing in behave the same), and the readout's Win box says what a spin paid. The reels are drawn at 0.65
  size with `zoom` on `.slots-reels`, so the 140px cells and offsets `slots.js` works in scale together. The deck
  has no panel or screws, just two rows on the case: the seven bet keys, then the readout. The LED frame is drawn with fewer lights along the top and
  bottom and more in the corners (`ledFrame(true)` in `slots.js`), so they stay about 12px apart all the way round. The whole machine fits one screen under the bar. The pay table, recent
  spins, season and biggest wins follow, one per row.
- **Blackjack:** the table first, a screen tall (the whole hand and its controls fit under the bar), then the
  table picker (`.casino-bar`, moved under the felt), then the side cards. The sound button is a round button in
  the felt's top right corner, like the slot machine's. The felt has no dealer, no table print and no tip row. Cards are 100px wide (88 and 56 as hands split; a hand of five or more closes its fan up to fit the width),
  the seven stake chips are 42px in one row with a plain Deal card under them (no fan of card backs, no shine, no
  hover), and the four moves share one row beside your bet's chip. Only the solo table has been checked.
- **Daily wheel:** the wheel across the whole screen (no card, title or description round it), the sound button
  in the top right corner, and the result line and a full-width Spin button under it, all on one screen. Prizes, your tokens and the two
  spin lists follow, one per row. The wheel is an SVG sized in percentages, so `wheel.js` needs no phone case.
- **Tap targets:** things you tap are at least about 30px tall (Form chips, odds buttons, nav entries).
- **Checking:** open the page in a phone-sized browser with touch (the Playwright `--device "iPhone 15"` profile),
  signed in so the chips show, and check dark first. The desktop page must be unchanged at 1920×1080.

## Navigation

- The top bar holds 🏠 **Overview** on its own, then four dropdown groups (`.nav-group` in `index.html`):
  📊 **Stats** (Players, Squad, Forecasts, Charts, Matches), 🎲 **Betting** (Place bets, Standings), 🃏 **Casino** (Slots, Onkey Stampede, Blackjack, Poker, Banana Hunt, Daily wheel) and 🐒 **Onkey's**
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
| A money form with a confirm step | `.transfer-form` + `.transfer-confirm` (Send credits and Onkey's Bank, in the Standings sidebar) | One row of labelled inputs ending in a button whose label ends in "…"; the first click shows a confirm line that spells out exactly what will happen (who gets how much, what you'll owe) with the real verb (Send, Borrow) and Cancel, and any edit removes the stale confirm line. Use it for anything that moves credits and can't be undone; a plain button is fine for paying money back |
| A click field | `.hunt-field` (Banana Hunt, `web/hunt.js`) | A fixed 1200 × 600 field centred in its card, always dark jungle green whatever the theme (like the arcade's machines). The server places the target and judges every click; the page only draws: a pick vanishes the target at once and floats a +1 at the click, then Onkey in his corner winds up and throws the next one in along an arc (the Web Animations API, 750 ms) and it lands with a squash before it can be picked. The target is a button, so the keyboard can pick it. Redraw the page only when the state changes shape (the daily cap), never per click. The field's ground and scenery follow the field of the day (`.hunt-theme-jungle` / `-night` / `-rain` / `-beach` / `-ruins`, emoji scenery from `SCENERY`), never the site theme. On it: a golden banana glows gold and pulses, greying just before it rots; a rotten decoy is the same banana in dull brown, so it has to be looked at; a bunch is five smaller bananas; a timed target shows a thin bar running down across the top of the field (`.hunt-clock`); Greg (`.hunt-greg`, the Greg logo) walks in from an edge and flies off when clicked; the combo sits in the top-left corner (`.hunt-combo`: the multiplier, the run and a bar to the next step, shaking when it breaks); a rotten pick greys the field with "Yuck!" until the freeze ends (`.hunt-frozen`). Results float up where you clicked (`.hunt-pop`: gold for a golden banana or the hidden item, light blue for a catch, red for a loss), and the streak and hidden item also get a toast. The tiles above are Today (credits against the day's cap), Combo, Streak and Hunt reopens (with the field's name) |
| Cards of different heights | `.bettor-slips` (CSS columns) | Stacks cards without holes; `break-inside: avoid` on each card |
| One list per player | `.ap-grid` > `.ap-col` (Charts, agent pool) | A column per player listing only what they have, instead of a sparse player × item grid |
| Line-up tables | `table.roster`, `tbody.drop-target[data-zone]`, `.grip`, `tr.slot-empty`, `tr.me` (Squad, `viewSquad()`) | Two tables with the same columns, Active squad above Bench. Rows drag between them (a ⋮⋮ grip in the first cell, the target tbody outlined with a dashed accent while a row is over it) and every row also has a Bench / Swap in button, so the page works without a mouse. The squad's open spots are dashed `slot-empty` rows that say what to do; a button that can't apply (full squad, minimum size) stays visible but disabled with a `title` saying why. Your own row is tinted like your Standings row. A line-up change posts the whole order, so dropping a row also reorders the colours |
| The house | `houseCard()` → `.house-card` (Standings sidebar, first), `.house-pots`, `.house-goals` (✓ met / ✗ missed / – no result), `.house-recent`; recap `house()` (`.status` pills) | The pot and jackpot as two small tiles, the next game's objectives as a count and prizes only (they're secret), the last game's objectives revealed with who got what (`plainName()`), the latest giveaways. Rules are folded in a `how()` |
| Odds boost | `.boost-card` (Place bets sidebar, under the odds format toggle), `.odd.boosted` | A sidebar card: the pick, then the usual price struck through beside its `oddBtn()`, then the rule. Wherever the boosted pick shows on the board, its button gets a static gold ring (`--warn`); a hot or cold streak's border wins over it. Added to a parlay, that leg carries the same ⚡ mark and a `--warn` accent stripe in the slip (`.parlay-leg.boosted`) and on its ticket (`.bet-leg.boosted`), and `parlayQuoteHtml()` notes the cap. The boosted button also carries a golden sheen (`.odd.boosted::after`: a faint gold wash with a band of light sweeping across, `boost-sheen`; a cold button keeps its frost glint instead), and inside the O / U pill of the player props it rounds off like a hot half, with a flowing gold ring in place of the flames (the pill gets `.lit`) |
| Onkey talks | `web/onkey.js` → `.onkey-says` under the top-left logo, `.brand.onkey-talking` / `.onkey-excited` | The casino dealer's voice site-wide: his noises in italics over a bold line in a card-coloured bubble, tail up to the logo, which hops and jabbers while he talks (three hops when excited). A line stays up about 8 seconds for 10 words (the dealer's about 7). Idle lines come every 70-140 s while the tab is visible, now and then an ominous one in a dark bubble that looks the same in every theme, while Onkey turns black and white under a shadow falling on him from above (darkest over his head, clearing down him, cut to his outline so the top bar around him is untouched) and sways slowly instead of hopping (`.onkey-ominous`, more often after midnight), reactions straight away after something you did (at the slots he cheers every win but says something about a spin that didn't win only rarely: he's company, not a nag); at the blackjack and poker tables he walks from the logo to the dealer's seat (the logo stays empty and the dealer's face waits for him; `.onkey-walker`, a waddle with a bob and tilt per step) and walks back when you leave, and he's silent there; idle lines make no sound, and clicking the bubble hushes him for 15 minutes. `aria-live` is off: it's chatter, not news. He notices the small things too, a pick added to the slip, a theme change, an equipped look, a prank, a max bet, a tease (he gasps along), an arcade score when you leave the machine, a sync, new squad games coming in, coming back to the tab, a hover over him, but those go through `chime()`: only some of the time and never within 12 s of his last line, so he feels alive without chattering over himself. New lines go in `IDLE`, `SAY` or `DYNAMIC`; pages report events with `FiveOnkey.note(kind, detail)` |
| A result still turning | `holdBalance()` / `releaseBalance()` / `displayBalance()` (app.js) | A spin the server has already settled (slots, the daily wheel) holds the balance shown in the top bar and on the machine (less the stake for slots) until its animation lands, so a background refresh can't give the result away; releasing it counts the chip up to the real balance. Any new game whose result is known before its animation ends should do the same |
| Wheel tokens in the bet slip | `tokenRow()` → `.slip-tokens` > `.token-btn` (`.on`), `.token-price`, `perkNote()` → `.bet-perk` on tickets (bets.js) | Shown on each single only while you hold a token: a pill per kind with the count; one token per pick, each token on one pick at a time, a boost greyed out on the game-boosted pick. In Parlay mode each leg shows only the Boost pill (`tokenRow(x, i, true)`); a boosted leg gets the gold edge and its boosted price, and a note under the price says what it was raised from and the 250-credit cap. Insurance is singles-only. Turned on, the pick shows its boosted price in gold and its to-win says so; the ticket names the token |
| A waiting reminder | `#me-wheel` (top bar), `.nav-alert` (a nav trigger or menu entry) | Something the signed-in bettor can do now that they'd miss otherwise (today: their daily wheel spin, `wheelReady()`): a gold chip in the top bar that goes there, hidden on that page, and a small pulsing gold dot on its nav group and menu entry. Both go away once it's done |
| The scientist | `window.sciNote(line, extra)` → `.sci-note` (common.js); `.brand.onkey-scientist` (the logo); `.onkey-bubble.tone-sci` (the dealer's chair); `.wheel-hand`; `.hunt-claw`; `.bet-slip-card.watched` | The villain of Onkey's lore (`docs/onkey-lore.md`). Wherever he shows, it's his face (`scientist-face.png`, round) and one line in lab green (`#16241a` ground, `#6f9f58` edge, `#dff3d3` text), fixed colours whatever the theme. He always speaks in his accent ("zis", "zat", "ze") and nobody else does. He is rare by design: a few percent of idle lines, one login in twenty, one house card in seven; never put him somewhere he'd show on every visit. His offer card (`offerCard()`, Standings sidebar) has one button, since the offer can only be refused |
| Prize wheel | `web/wheel.js` → `.wheel-layout`, `.wheel-row`, `.wheel-box` (SVG `#wheel-rotor`, `.wheel-slice.wk-<kind>`, `.wheel-pointer` hanging from `.wheel-pivot`, `.wheel-center`: the jackpot in the hub, a starred title over a gold number with a sheen, sized in `cqw` of `.wheel-box`), `.wheel-prizes` | The Daily wheel page: the wheel is the one big thing (up to 960px and never taller than the window, starting at the top of its card with the title and speaker in its empty corners; the wheel is centred in its card, the result line sits in the space to its right (`.wheel-controls`, out of the flow) and the Prizes column is a narrow 280px, so everything fits a 1080p screen), a slice's angle is its real chance (so the jackpot is a thin gold sliver with a halo and a bright centre line; no marker over it), the jackpot amount in the hub, and the prize table (rarest first), tokens and spin lists beside and below. Slice colours are `--wheel-*` tokens, as are the medium grey rim (`--wheel-rim`, its bulbs on its outer edge, warm white, red and blue in turn (`--bulb-1`…`--bulb-3`), none behind the flapper) and the black flapper hanging from a small silver pin (`--wheel-flapper`). The spin is a physics simulation played back frame by frame (momentum, air drag, rolling friction, and pegs that must push a sprung flapper aside), solved so it comes to rest in the server's slice: it never jerks, and a tease is just the wheel reaching the deciding peg with barely enough momentum to creep over it, or barely too little so the flapper pushes it back (the stage darkens, bulbs pulse gold, a drone and heartbeat play while it crawls), on wins and misses alike. The pointer is that flapper, short enough that its tip only just reaches the pegs, which straddle the wheel's edge, half on the slices and half on the rim (it never passes through them): pegs bend it and it springs back with a damped wobble. Each prize has its own sound and screen effect (`celebrate()`), the jackpot all of them: dim, triple flash, shake, banner, gold rain. At rest the bulbs run a slow chase and the jackpot's halo shimmers, the only idle animation besides the slots cabinet. The angle and the flapper's bend are kept across redraws. The wheel itself is the Spin control: while a spin is waiting it's grabbable (`.wheel-box.grabbable`, a ring on the rim and a dotted arrow, `.wheel-grab-hint`), you drag it and let go to throw it, and a throw that's too soft leaves it where it was. There is no Spin button and no text explaining it: the hint on the rim is the whole instruction, and once the day's spin is used the controls column says only "Next spin in …". A harder throw only spins longer. The wheel wears the jungle look: leaf greens for credits, banana yellow, a wooden rim (`.wheel-plank` seams) with firefly bulbs, a dark wood hub, and a leaf for the flapper (`.wheel-pointer`, an inline SVG leaf hanging from a wooden pin). Screen effects scale with the prize: a flash and confetti for most, a banner, bursts and a shower of coins for the top credits slice (`BIG_CREDITS`, about 4 seconds, `BIG_MS`), and for the jackpot a dimmed page, banner, confetti and rain for about 9 seconds (`JACKPOT_MS`). Mute is `fs.wheelMuted` |
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
little when pressed. Chrome is `--slot-chrome` / `--slot-chrome-dark`. There's no footer. Two gold arrows (`.slots-line-arrow`) mark the centre line, with no rule drawn across the symbols (thin lines over the reels were distracting); the reels' shading leaves the middle row brightest;
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
(about 1 spin in 272, and it always pays when it does).
They accelerate, then decelerate with matching incoming velocity and stop on the
server's result, one at a time in `stopOrder()`'s order: a matching pair first
(either of the two first) when the result has one, so the last reel to stop is
always the one that decides the spin; with no pair, any order, and the last reel
follows 120 ms behind the second instead of keeping you waiting. The reels show
your last spin when the page loads (`reelsNow()`). Space spins too (the Spin
button's shortcut, ignored while a control or text field has focus). Repaints retain their positions; leaving the tab
cancels motion.

The tease: when the first two reels to stop match, the last may keep spinning after the
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
heartbeat while the line's arrows flicker red (`.slots-glass.teetering`), then snaps a
half cell with a little overshoot, a thump and a nudge of the cabinet. On a win it
snaps onto the match from either side; on a loss the last reel lands on a cell of
its real result next to the pair's symbol where the strip has one, and snaps back
when the match was just short (below) or forward when it had crept just past
(above): a deliberate near miss. It only picks between identical-looking cells,
never changes a result or the odds. The reel then flashes gold with a crash and a
rising sting, or red with a sad trombone (`.tease-won` / `.tease-lost`).
Wins light the cabinet, the winning row (a gold glow behind the symbols,
`.slots-win .slots-reel::before`) and the matching pay-table row. The marquee glows while
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

The Golden Onkey is wild and pays when spotted (`payouts()` in `slots.py`; the page
mirrors the rule in `lineOf()` / `matches()` for the stop order and the tease, and
reads what was paid from the spin's `parts`). The moment its reel stops it pops
(`landSpotted()` / `spotPop()`: a gold ring burst on the reel and a gold "Spotted!"
pill, `.slots-spot-tag`, springing up at its top, with a two-note glint, higher for
the second one), and it keeps the ring, the glow behind it and the tag while the
result shows (`.slots-reel.spotted`). The redraw after the spin picks the pop up
where it was (`spotAt`, Web Animations `currentTime`), so it plays once without a
restart. A miss on a tease that still lands a Golden Onkey ends on its glint, not
the trombone or the red flash.

A win's payouts are cash-out chips side by side where the result line sits
(`cashouts()`, `.slots-cashouts`): one `.slots-cash` per part, the line (its three
symbols, the Golden Onkey drawn in where it filled in, "Bell line (wild) · 4×",
"+40") and, in gold, what the Golden Onkey adds: multiplying the line ("Golden Onkey
wild · line ×2", "+40"; ", capped at 100×" when the cap cut it) or, alone, spotted ("Golden Onkey spotted · 2×"), joined by
"+", and with more than one part "=" and a gold total chip ("8× in all"). A fresh win (`freshId`, only the
first draw after it lands) deals them in 120 ms apart with a springy pop
(`slots-cash-in`, `--k`) and a rising coin tick each (`sound('cash', k)`), then
counts the total up (`cashIn()`), all inside about a second, so two payouts read as
one result rather than a queue. Their celebrations run together too: the line's
tier and golden sparkles out of each spotted reel at once.
The moving parts live in a fixed `.slots-fx-layer` that removes itself, so they
play once and never replay on a redraw. Idle animation is welcome on the cabinet, which should feel like a lit machine
on a casino floor: the LED chase runs at rest. Everything else on the page stays
still until it's used. Results use a live status region and errors appear inline. An uncertain
request locks stakes and offers "Check last spin"; "Sign in to spin" uses the
existing account menu.

### Onkey Stampede

`#stampede` lives under Casino, after Slots. It's the bigger, louder machine, and the page is laid out
like Slots: the cabinet starts the page, a 360px sidebar card holds the pay table (each symbol's ×3 / ×4 / ×5 in
credits at the stake picked, the wild, spikes, fireballs, the fire meter, the jackpots and events folded in a
`how()`, and the latest jackpots), and three cards sit under the cabinet in the same column (your recent spins with
feature chips, your season, the season's biggest wins), so nothing leaves a gap beside the tall sidebar.

The cabinet (`.st-cabinet`) is dark in every theme, like the classic one, but its own palette: ember browns, fire
and gold (`--st-*`), turned purple and orange in October (`.st-october`, with the pumpkin Onkey as the top symbol),
deep blue during free spins (`.st-free`) and red-hot during hold and spin (`.st-hold`). From the top: the four
jackpots (Grand widest, then Major, Minor, Mini, each its own colour and emblem, in credits, the same for everyone,
ticking up as the squad spins), your fire meter (`.st-meter`: a flame bar with the count and how often the pick
comes at the bet you've picked, glowing near full), the title with
Onkey peeking over it, the 5 × 4 reel window, a result line, and the deck (bet keys, a Credits / Bet / Win readout,
Auto and Turbo, and a round Spin button that reads Skip while a spin plays). Symbols are drawn, not emoji:
carved-looking letters for 9 to A, SVG coconut, bongo drums, banana bunch and Valorant tile, and pictures for Greg,
the Golden Onkey, the Bongo Onkey wild (with a gold WILD tag), the spike and the fireball (with the credits it pays
printed on it). Higher symbols get a soft halo.

Each jackpot has an emblem (`emblem()`, `.st-emb-*`): a coin in its colour with a picture, never a bare word. Mini
is a banana bunch on green, Minor a coconut on blue, Major the Golden Onkey on purple, Grand the charging Onkey in a
crown on red; the pick's Smoke is a grey cloud. Use them wherever a jackpot is named.

Being honest about money is a rule here: a spin is only a "Win!" (gold) when it paid more than the bet. A payout
under the bet reads "7 of your 10 back" in muted text, its cells light without the pulse, and it gets no win
sound. Celebrations (sounds, Inferno, Big / Mega / Epic) are for real wins.

Motion carries the dopamine, so the rules are about keeping it readable: reels blur while moving and bounce on
landing; a reel that could finish a bonus glows and spins on (a tease from what's showing, never from the result);
events have one clear beat each (Onkey charging across with dust and a rumble, wilds stamping in one by one,
bananas falling then fireballs dropping into cells, flames up the window with a banner); every fireball throws a
spark into the meter; wins light their cells and dim the rest, then take turns after the spin; banners are short,
centred over the reels and gone in about a second and a half. A bonus explains itself before it starts: hold and
spin opens with a rules card (`.st-howto`: three short lines), then swaps the title for a display of respins left,
fireballs out of 20 and the bonus so far (`.st-hud`), with empty cells visibly spinning and stopping reel by reel,
a note for each respin ("+2 fireballs! Back to 3 respins", "1 respin left") and a collect into the Win at the end.
The jackpot pick (`.st-pick`) covers the reels with fifteen fireballs and a tracker of three pips per kind; a
kind with two lights up; "Pick for me" picks one every half second. Big wins (10×, 25×, 50×) and jackpots get a
full overlay on the reels with a counting number and coins. Turbo shortens everything, and Spin or Space skips
ahead. After an Epic win or a Major or Grand jackpot, Onkey sings an encore (one of his own recordings) under the
result line, "Onkey is singing for you", until the next spin; it's kept for those moments so it stays special.
The phone layout doesn't cover this page yet.

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
- **Size**: the tables are the page, so they're big. Blackjack's felt fills the screen under the bar (`.bj-felt`,
  `min-height: clamp(720px, 100vh - 230px, 860px)`: the cap keeps a taller window from opening a gap between the
  table print and the spots), with the spare space between the print and the spots; poker's oval is
  660px tall. Both fit 1920×1080 without scrolling, demo banner included, except the shared blackjack table when a
  seat wraps its split hands onto a second row. Onkey is 112px, his bubble 16px text.
- **Cards** (`FiveCasino.card()`, `.pcard`): drawn in CSS, no images: rank and suit top left, a big pip bottom right,
  red for hearts and diamonds; sizes `xs` (logs and tables), `sm` (22-34px samples), `md` (other poker players,
  blackjack hands once there are many), `lg` (your poker hand, the shared blackjack table), `xl` (the poker board,
  100px; Onkey's blackjack hand and yours at the solo table, 120px via `.bj-felt .pcard.xl`). Overlapping blackjack cards overlap by a third of their width.
  Splits have no limit: a seat's hands sit side by side and wrap, the card size steps down as hands are added, and
  the shared table's seats stay on one row (`.bj-spots.shared`, nowrap). A face-down card is `.back`, an empty board slot `.empty`. Every card has an
  `aria-label` ("Ace of spades").
- **Dealing** (`FiveCasino.deal()`): moves are settled on the server at once but played out on the page. Every
  table card has a `key` (table, round or hand, seat, position) and a `seq` (its place in the real deal order:
  two rounds round the table, then hits and splits, Onkey's hole card turning over, then his draws; at poker the
  hole cards from the left of the button, a showdown's flips, then the board). New cards fly in from Onkey one at a
  time (`.deal-in`, 380 ms apart) and a face-down card turns over (`.flip-in`); a card re-rendered mid-flight keeps
  its place (a negative delay). Totals, results, hand names, the status line and the controls carry `.after-deal`
  until the last card lands (`FiveCasino.after()`), and Onkey's line, sounds, Table win bursts and the credits chip
  wait for it too. Opening a table shows what's on it without dealing it again.
- **Onkey, the dealer** (`FiveCasino.dealer()` / `say()`): his picture with a white speech bubble to its right. When
  he speaks, the bubble's first line is his monkey noises ("Ook Eek Ook", with "!" when he's excited) and the second
  what they mean, while Web Audio plays matching ooks (low, falling) and eeks (high, rising); his picture jabbers. He reacts to the table's log, one line per update, the most interesting event first
  (`PRIORITY`); his lines are `QUIPS`, casual and short. One mute button (`fs.casinoMuted`) covers both tables.
- **Turns**: the player to act gets a gold outline (`--turn`) and a shrinking `.timer-bar`; countdowns
  (`[data-deadline]`) tick every 250 ms against the server's clock and go gold, then red, under 6 seconds.
- **Controls**: stakes are round gold chips (`.stake-key`); moves are big `.casino-act` buttons with the amount as a
  second line (Hit / Stand / Double / Split; Fold / Check or Call / Raise to), the primary move (Hit, Raise) in the
  accent colour and Fold in red text. Poker's raise has a slider, a number field and presets (Min, ½ pot, Pot, All
  in). Keyboard shortcuts: H S D P and Enter at blackjack, F C R at poker.
- **Chips** (`FiveCasino.chip()` / `chipFace()` / `denom()`, `.dn-5` ... `.dn-500`): drawn like real casino
  chips as a small SVG (so they stay sharp at any size), one colour per value: eight edge spots round the rim (a dashed
  stroke, so the outline stays a true circle), a dashed ring, a pale inlay with the value centred on its height (smaller
  type for three and four digits). 5 red, 10 blue, 25 green, 50 orange, 100 black with gold spots, 250
  purple with gold, 500 gold. Stake keys, the chips on a hand, your bet beside the buttons and poker's bets use them;
  an amount between denominations takes the largest it covers. A bought chip style (`.chp-*`) wins.
- **The table print** (`feltPrint()`): as on a real layout, BLACKJACK PAYS 3 TO 2 in gold serif capitals on the
  centre line of a dark band edged in gold that curves round the dealer (sized to the lettering), DEALER MUST STAND ON ALL 17s in a smaller arc
  beneath. Both are arcs of circles round one centre (`FP`), so the lettering follows the band and the lower line runs
  exactly parallel (inline SVG, `textPath`). It sits right under Onkey's hand; the felt's spare height goes between it
  and the players' spots, so each spot, the result line under it (12px) and the controls stay together at the bottom.
- **Blackjack's action buttons** (`.bj-act`): one size for all four (136 × 60), each filled with its move's colour
  (Hit green, Stand red, Double gold with dark lettering, Split blue) over a darker base it sinks into when pressed; the
  extra stake on a second line for Double and Split; the shortcut key in the tooltip. The four are centred on the
  table as a group (`.bj-act-group`), and the extras hang outside it: your bet is a chip to their left
  and, at the solo table, your streak (🔥 / 🧊) to their right. Poker keeps `.casino-act`.
- **Tipping Onkey** (`tipRow()` / `tipHtml()`): after a round you won, a pill to Onkey's left: "Tip Onkey" and 5 / 10 / 25 chips
  (dimmed above what you won); tipping turns it into "🍌 You tipped Onkey 5." and Onkey thanks you (`tip`, bigger
  thanks at 25).
- **Your bet mid-hand** sits to the left of Hit / Stand / Double / Split (`.wager`, the round's stake as a chip); the
  hands themselves show no chips until they're settled, when the stake comes back with its winnings or is swept away.
  Under each hand the total is centred under the cards and nothing else stays there: when the hand settles its chips
  play out beside the total and vanish (a loss is swept off to Onkey; a win's winnings fly over from him and both chips
  slide back down to you; a push slides back). The status line names the result (Win, Lose, Push, Blackjack, Bust; a
  split lists each hand) with the round's net and nothing else at the solo table, and a hand that lost is dimmed.
  The hand being played is only highlighted when a split has left more than one; your bet chip sits level with the
  middle of the action buttons, its label hanging underneath.
  The solo table has no name on your spot (it's yours) and no emote tray; the shared table has both.
- **The Deal button** (`dealBtn()`): a cream card with gold lettering ("DEAL", or "BET" / "BET IN" at the
  shared table) and corner pips, on a fan of card backs that spreads on hover; a shine sweeps the face, pressing it
  flicks the top card. It never shows the stake; the picked chip does. Side bets are the round spots either side
  of it (`sideSpot()`: Pairs, 21+3; tap to add a chip, right-click to clear), their results pills under the hand;
  they're switched off for now (`side_bets.open` from the server), which hides the spots and their rules.
- **Chips that move** (`motion()` in `blackjack.js`): a bet slides in, winnings fly over from Onkey's side as a
  second chip, a lost stake (or side bet) is swept up to him. Each plays once; a redraw mid-flight keeps its place.
- **Streaks and emotes**: three wins in a row and a seat glows like a flame (`.bj-spot.hot`, 🔥 count); three
  losses and it frosts (`.cold`, 🧊). Onkey turns salty or sympathetic at 3, 5, 8 and 12. The emote tray sits in
  the felt's top-left corner; an emote floats up from the sender's seat, a banana spins over and bonks Onkey.
- **Onkey's save** (blackjack, 1 bust in 100): the card that busted you lands as dealt, then Onkey draws on it in
  blue pen: an X strikes through the number in the corner and the number that makes 21 pops on next to it, crooked,
  in a handwriting font (about 1.9 s from the deal; `.fix-in`). The suits stay
  as they are. The total, the result and his line ("Onkey has a pen and no shame.") wait until he's finished.
- **Greg in the chair** (casino.js, both tables): 1 time in 20 that Onkey walks to a table, Greg is sitting in the
  dealer's chair when you get there (his face, "*Greg clears his throat*" and a line). Onkey waits in the logo until
  Greg has said it (about 3 s), then walks over and throws him out: Greg spins off the felt, Onkey wobbles into the chair
  and has his say.
- **Onkey's hint** (blackjack): not every time: about one pause in three, at most once a round and never in the round
  after one. 3 seconds into your move (after the cards land), Onkey suggests the move the numbers favour
  in his bubble, as advice, never figures: plain ("{total} won't hold up. Hit it."), a joke ("Split them like a
  banana. Right down the middle."), or the dealer slipping you help he shouldn't ("Don't tell the house Onkey said
  this: hit."), about a third each; sarcasm when the call is obvious and you're still thinking ("You've got 20. You're
  really thinking about this?"); a dig at his own weak upcard when you should stand against a 2-6 ("Onkey's showing a
  5. Stand and let Onkey sweat."); and a shrug when the top two moves are within 1 per 100 ("Tough one. Onkey's gut
  says hit.") (`HINTS` in `blackjack.js`). Onkey is the dealer, so every table line has him talking about himself
  ("Onkey", "I"), never "him" or "the dealer". Now and then (15% of decisions) the pause brings a peek instead: Onkey names the next card in the shoe or his own
  hole card ("Psst. A 5 on top of the shoe. You didn't hear it from Onkey."), lying 40% of the time, and once the
  card shows he gloats or owns up ("Told you. A 5." / "Gotcha. A 3, not an 8. Never trust Onkey."). A peek lights no
  button, and he only follows up claims he actually said. The hint's button gets `.hinted`, a
  breathing gold ring. Once per decision; it never moves for you.
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
- Money has three kinds and the page keeps them apart: credits (betting, rewards, loans), bananas (whole numbers, from games
  played, the shop's currency) and debt (what's owed to Onkey's Bank, shown in red with a minus, in its own Rankings column).
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
- Don't code for reduced motion: no `prefers-reduced-motion` media queries in `style.css`, no `matchMedia` checks in
  the JS and no still fallbacks for animations. This site has no need for it, and the handling was removed everywhere
  on 2026-10-03. Animations always play.

## Onkey's help at blackjack

His hints and his peeks at a card come in a gold-tinted bubble (`tone-gold`), so they stand apart from table talk;
the follow-up once the card shows is gold if he told the truth and red (`tone-red`) if he lied. The peek itself is
always gold: the page isn't told it's a lie until the card is out. `say()` in `casino.js` takes the tone, and passes
it to the logo's bubble on a phone.

## Sound buttons

Every sound toggle shows the same icon, `speakerIcon(off, size)` from `common.js`: a speaker with sound waves, or
crossed out when muted, in the button's text colour. Slots, the casino tables and the daily wheel use it alone
(with an `aria-label`); the arcade puts "Sound on" / "Sound off" beside it. Each page keeps its own mute setting.

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

- **Onkey Stampede** (2026-10-04) was checked in dark and light at 1920×1080 with headless Chrome: a plain win, a
  Stampede, free spins and a forced Grand. Its sound clips haven't been heard together in a real browser yet (the
  headless checks only confirm they load and play without errors), and it has no phone layout yet.

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
  sounds, and the shop themes are still to be checked. The bigger tables, re-splits and the hint (2026-10-01) were
  checked in dark only: blackjack at four split hands solo and a three-hand seat at the shared table (states built
  with the engine and served to the page), poker preflop with two players.
- Not yet reviewed: the signed-in top bar and bet slip (the demo has no account to sign in with), and the shop themes
  after the width change. Light mode has been checked on Forecasts and Charts.
- The Squad page (2026-09-30) was checked in dark at 1920×1080 with a headless browser against the fake API, and its
  drag and drop only through the buttons that do the same thing; a real mouse drag, touch, and light mode are still to
  be checked. The demo squad is fixed, so the editable page needs a real API key (or the self-test's fake client).
