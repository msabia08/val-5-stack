/* 5-Stack Tracker: the betting UI. The bet slip, bet tickets, the Standings page (view bettors: rankings, report card, settled bets,
 * rewards, seasons) and their data loading, split out of app.js the way the charts live in viz.js.
 *
 * app.js calls FiveBets.init() once with the helpers this needs (state, api, draw, ...), then uses the functions
 * returned at the bottom. Loaded before app.js; plain JS, no dependencies.
 */
window.FiveBets = (() => {
  'use strict';

  // Shared with app.js (set by init): the page state object and its helpers.
  let state, $, $$, api, bettorSlot, draw, esc, fmt, kpi, memberIndex, plainName, toast;
  // From Onkey's Shop (web/shop.js): a bettor's name in their bought colours and badge, and their ticket style.
  let nameHtml, ticketClass, ticketExtras;
  // A one-line explanation with the rest folded behind "How this works" (the full text is still one click away).
  const how = (summary, more) => `<details class="how"><summary>${summary}</summary><div class="how-body">${more}</div></details>`;

  function init(ctx) {
    ({ state, $, $$, api, bettorSlot, draw, esc, fmt, kpi, memberIndex, toast } = ctx);
    plainName = ctx.plainName || ((name) => esc(name));
    nameHtml = ctx.nameHtml || ((name) => esc(name));
    ticketClass = ctx.ticketClass || (() => '');
    ticketExtras = ctx.ticketExtras || (() => '');
    state.transfer = { to: '', amount: '', note: '', confirm: false }; // the Send credits form, kept across redraws
    state.loan = { amount: '', repay: '', confirm: false }; // Onkey's Bank's forms
  }

  // ---- data -------------------------------------------------------------------
  const loadBettingReport = async () => { state.bettingReport = await api('/api/betting-report'); };
  const loadSeasons = async () => { state.seasons = await api('/api/seasons'); };
  const loadBets = async () => {
    const [b, l, m, r, t, h, k] = await Promise.all([api('/api/bets?limit=600'), api('/api/bettors'), api('/api/bettor/me'),
      api('/api/rewards?limit=60'), api('/api/transfers?limit=30'), api('/api/house').catch(() => null), api('/api/bank')]);
    state.bets = b.bets; state.bettors = l.bettors; state.me = m.bettor || null; state.rewards = r.rewards; state.transfers = t.transfers;
    state.house = h;
    state.bank = k;
    if (state.me) { state.bettor = state.me.name; localStorage.setItem('fs.bettor', state.bettor); }
  };
  const isMine = (b) => !!state.me && b.bettor.toLowerCase() === state.me.name.toLowerCase();

  // ---- bet slip and tickets -----------------------------------------------------
  // What the slip's stake leaves you with, or how far over your balance it goes.
  function afterStake(total) {
    if (!state.me) return '';
    const left = state.me.balance - total;
    return left >= 0 ? `Leaves you ${fmt.credits(left)} credits` : `<span class="down">${fmt.credits(-left)} more than you have</span>`;
  }

  // A parlay's price comes from the server (/api/odds/parlay): legs that tend to land together are priced together,
  // and legs that decide each other are refused. Until it answers, the slip shows the legs' odds multiplied.
  let parlayQuote = null; // {key, quote} or {key, error} for the slip's legs and odds context
  let parlaySeq = 0, parlayAsking = null; // parlayAsking: the key of the request on its way
  // A leg can carry a daily wheel boost token (x.boost, toggled on the leg), never on the game-boosted pick.
  const legBoost = (x) => !!x.boost && !x.gameBoost;
  const parlayLegs = () => state.slip.map((x) => ({ market_id: x.market_id, selection: x.selection, ...(legBoost(x) ? { boost: true } : {}) }));
  const parlayKey = () => JSON.stringify([state.slip.map((x) => [x.market_id, x.selection, legBoost(x)]), state.ctx]);
  const currentQuote = () => (parlayQuote && parlayQuote.key === parlayKey() ? parlayQuote : null);
  const parlayBlocked = () => !!(currentQuote() || {}).error;
  function parlayDecimal() {
    const q = (currentQuote() || {}).quote;
    return q ? q.odds_decimal : state.slip.reduce((a, x) => a * Number(x.decimal), 1);
  }
  const parlayToWin = (stake) => (parlayBlocked() ? '' : `To win ${fmt.credits(stake * (parlayDecimal() - 1))}`);

  function parlayQuoteHtml() {
    const pq = currentQuote(), q = pq && pq.quote;
    const linked = new Set(q ? q.linked.flat() : []);
    const boostIdx = q && q.legs ? q.legs.findIndex((l) => l.boost && !l.boost_token) : -1; // the odds boost of the game
    const legs = state.slip.map((x, i) => {
      const token = legBoost(x), lit = i === boostIdx || token;
      return `<div class="slip-item parlay-leg${linked.has(i) ? ' linked' : ''}${lit ? ' boosted' : ''}"><div><div class="slip-desc">${lit ? '⚡ ' : ''}${esc(x.desc)}</div>` +
        `<div class="muted small">${esc(x.selLabel)} @ ${esc(x.american)}${token ? ` <b class="token-price">⚡ ${tokenPrice(x).toFixed(2)}</b>` : ''}${linked.has(i) ? ' · <span class="linked-tag">linked</span>' : ''}</div></div>` +
        `<button class="btn ghost icon rm" data-i="${i}" aria-label="Remove">✕</button>${tokenRow(x, i, true)}</div>`;
    }).join('');
    const dec = parlayDecimal();
    const notes = [];
    if (!pq) notes.push('<p class="muted small">Checking how these legs go together…</p>');
    else if (pq.error) notes.push(`<p class="small down parlay-blocked">${esc(pq.error)}</p>`);
    else {
      if (q.odds_decimal < q.independent_decimal) {
        notes.push(how(`Linked legs: odds cut from ${fmt.oddsDec(q.independent_decimal)} to ${fmt.oddsDec(q.odds_decimal)}.`,
          `The legs marked "linked" won together more often than chance over the last ${q.games} games at today's lines ` +
          `(a player's kills and ACS, say), so multiplying their odds would overpay. The price is cut by how much more often ` +
          `they all landed together than they would have if they were unrelated, pulled toward "unrelated" when there are few ` +
          `games. Odds are only ever cut, never raised, and a parlay never pays less than its longest leg.`));
      }
      if (boostIdx >= 0) {
        const cap = (state.odds && state.odds.boost && state.odds.boost.max_stake) || 250;
        notes.push(`<p class="muted small">⚡ Odds boost of the game, up from ${fmt.oddsDec(q.legs[boostIdx].boost)} on "${esc(q.legs[boostIdx].description)}". A parlay with it is capped at ${fmt.credits(cap)} credits.</p>`);
      }
      const tokenLegs = q.legs.filter((l) => l.boost_token);
      if (tokenLegs.length) {
        notes.push(`<p class="muted small">⚡ Boost token: ${tokenLegs.map((l) => `"${esc(l.description)}" up from ${fmt.oddsDec(l.boost)} to ${fmt.oddsDec(l.odds_decimal)}`).join(', ')}. ` +
          `The parlay's price is worked out from the boosted price, and it's capped at ${fmt.credits(TOKEN_CAP)} credits.</p>`);
      }
    }
    return `${legs}<div class="parlay-summary"><span>${state.slip.length}-leg parlay</span>` +
      `<b>${pq && pq.error ? '–' : fmt.oddsDec(dec)}</b></div>${notes.join('')}`;
  }

  async function fetchParlayQuote() {
    const key = parlayKey();
    if (currentQuote() || parlayAsking === key) return;
    const seq = ++parlaySeq;
    parlayAsking = key;
    let next;
    try {
      const quote = await api('/api/odds/parlay', { method: 'POST', body: JSON.stringify({
        legs: parlayLegs(), context: state.ctx }) });
      next = { key, quote };
    } catch (e) {
      next = { key, error: e.message };
    }
    if (seq !== parlaySeq) return; // the slip changed while this was on its way
    parlayAsking = null;
    parlayQuote = next;
    const box = $('#parlay-quote');
    if (!box || parlayKey() !== key) return;
    box.innerHTML = parlayQuoteHtml();
    bindRemove(box);
    bindTokens(box);
    const tw = $('.parlay-towin');
    if (tw) tw.textContent = parlayToWin(Number($('#parlay-stake')?.value) || 0);
    const place = $('#place-bets');
    if (place && state.me) place.disabled = !!next.error;
  }

  // The daily wheel's tokens (bets.py TOKEN_BOOST / TOKEN_MAX_STAKE, counts in /api/bettor/me tokens): each single in
  // the slip can take one boost and one insurance token, as many as you hold, toggled on the pick itself; a parlay's
  // legs can each take a boost token (never insurance).
  const TOKEN_BOOST = 0.5, TOKEN_CAP = 250;
  const tokensHeld = (kind) => (state.me && state.me.tokens ? state.me.tokens[kind] || 0 : 0);
  const tokensFree = (kind, except) => tokensHeld(kind) - state.slip.filter((x, i) => i !== except && x[kind]).length;
  const tokenPrice = (x) => (x.boost ? Math.round((1 + (x.decimal - 1) * (1 + TOKEN_BOOST)) * 100) / 100 : x.decimal);
  function toWin(x) {
    const win = `To win ${fmt.credits(x.stake * (tokenPrice(x) - 1))}${x.boost ? ' with the boost' : ''}`;
    const notes = [];
    if (x.boost && x.stake > TOKEN_CAP) notes.push(`<span class="token-warn">A boost token covers up to ${fmt.credits(TOKEN_CAP)} credits</span>`);
    if (x.insurance) notes.push(`stake back if it loses, up to ${fmt.credits(TOKEN_CAP)}`);
    return [win, ...notes].join(' · ');
  }
  function tokenRow(x, i, leg) { // `leg`: a parlay leg, which only takes a boost token
    if (!tokensHeld('boost') && (leg || !tokensHeld('insurance'))) return '';
    const btn = (kind, label, why) => {
      const on = !!x[kind], free = tokensFree(kind, i) > 0;
      const off = !on && (!free || why);
      return `<button type="button" class="token-btn${on ? ' on' : ''}" data-token="${kind}" data-i="${i}" aria-pressed="${on}" ${off ? 'disabled' : ''}` +
        ` title="${esc(why || (free || on ? '' : `No ${kind} tokens left for this slip`))}">${label}</button>`;
    };
    return `<div class="slip-tokens">${tokensHeld('boost') ? btn('boost', `⚡ Boost ${tokensHeld('boost') > 1 ? `(${tokensHeld('boost')})` : ''}`, x.gameBoost ? 'This pick already has the odds boost of the game' : '') : ''}` +
      `${!leg && tokensHeld('insurance') ? btn('insurance', `🛡️ Insure ${tokensHeld('insurance') > 1 ? `(${tokensHeld('insurance')})` : ''}`, '') : ''}</div>`;
  }

  function slipHtml() {
    // Only the picks: signing in lives in the account menu (the profile chip, top right) and the balance in the
    // credits chip beside it.
    const me = state.me;
    // On a phone the slip is a bar pinned to the bottom of the screen that opens into a sheet (style.css's phone block,
    // html.slip-open; the heading is its handle and shows how many picks it holds). An empty slip isn't shown there,
    // so it closes.
    if (!state.slip.length) document.documentElement.classList.remove('slip-open');
    if (!state.slip.length) {
      return `<h2>Bet slip</h2><p class="muted">Tap any odds to add a pick.</p><p class="muted small">Picks placed up to ${fmt.n0(state.status.bet_grace_minutes ?? 2)} min after a game starts still count for it. You can cancel a pick for ${fmt.n0(state.status.bet_cancel_minutes ?? 1)} min after placing it.</p>`;
    }
    const canParlay = state.slip.length >= 2;
    const mode = canParlay ? state.slipMode : 'single';
    const modeToggle = canParlay
      ? `<div class="slip-mode" role="tablist">
          <button type="button" class="mode-btn ${mode === 'single' ? 'on' : ''}" data-mode="single" role="tab" aria-selected="${mode === 'single'}">Singles</button>
          <button type="button" class="mode-btn ${mode === 'parlay' ? 'on' : ''}" data-mode="parlay" role="tab" aria-selected="${mode === 'parlay'}">Parlay</button>
        </div>`
      : '';
    let body, placeLabel;
    if (mode === 'parlay') {
      const stake = state.stake;
      body = `<div id="parlay-quote">${parlayQuoteHtml()}</div>
        <label>Stake<input type="number" min="1" step="1" value="${stake}" id="parlay-stake" aria-label="Parlay stake"></label>
        <div class="muted small parlay-towin">${parlayToWin(stake)}</div>
        <div class="small slip-after">${afterStake(stake)}</div>
        <p class="muted small">All ${state.slip.length} legs must win. If one is voided (a push), the payout uses the odds of the legs that stood.</p>
        ${tokensHeld('insurance') ? '<p class="muted small">Insurance tokens work on singles only: switch to Singles to use one.</p>' : ''}`;
      placeLabel = 'Place parlay';
    } else {
      const items = state.slip.map((x, i) =>
        `<div class="slip-item"><div><div class="slip-desc">${esc(x.desc)}</div><div class="muted small">${esc(x.selLabel)} @ ${esc(x.american)} (${Number(x.decimal).toFixed(2)})${x.boost ? ` <b class="token-price">⚡ ${tokenPrice(x).toFixed(2)}</b>` : ''}</div></div>` +
        `<input type="number" min="1" step="1" value="${x.stake}" data-i="${i}" class="stake" aria-label="Stake">` +
        `<button class="btn ghost icon rm" data-i="${i}" aria-label="Remove">✕</button>` +
        `<div class="muted small towin">${toWin(x)}</div>${tokenRow(x, i)}</div>`).join('');
      const total = state.slip.reduce((a, x) => a + (Number(x.stake) || 0), 0);
      body = `${items}<div class="slip-total">Total stake ${fmt.credits(total)}</div><div class="small slip-after">${afterStake(total)}</div>`;
      placeLabel = `Place ${state.slip.length} bet${state.slip.length > 1 ? 's' : ''}`;
    }
    return `<h2>Bet slip <span class="slip-count">${state.slip.length}</span></h2>
      ${modeToggle}
      ${body}
      ${me ? `<button class="btn primary" id="place-bets" ${mode === 'parlay' && parlayBlocked() ? 'disabled' : ''}>${placeLabel}</button>`
        : '<button type="button" class="btn primary" data-signin>Sign in to place</button>'}
      <button class="btn ghost" id="clear-slip" style="width:100%;margin-top:6px">Clear slip</button>`;
  }

  // ---- bet tickets (slip-style rendering, grouped by bettor) --------------------
  // A single's boosts and insurance, from its context: a daily wheel token or the odds boost of the game.
  function perkNote(b) {
    if (b.market_type === 'parlay') return '';
    let ctx = {};
    try { ctx = JSON.parse(b.context || '{}'); } catch (e) { return ''; }
    const notes = [];
    if (ctx.boost) notes.push(`${ctx.boost_token ? '⚡ Boost token' : '⚡ Odds boost of the game'}: up from ${fmt.oddsDec(ctx.boost)}`);
    if (ctx.insured) notes.push(`🛡️ Insured: stake back if it loses, up to ${fmt.credits(ctx.insured.max)}`);
    return notes.length ? `<div class="small bet-note bet-perk">${notes.join('<br>')}</div>` : '';
  }
  // stamp: a ticket placed a moment ago lands with a "Placed" stamp (only in the sidebar's "Your open bets").
  function betTicket(b, stamp = false) {
    const ctx = b.market_type === 'parlay' ? JSON.parse(b.context || '{}') : null;
    const legs = ctx ? ctx.legs || [] : null;
    // Linked legs were priced together (see parlayQuoteHtml): say what the odds were cut from.
    const cut = ctx && ctx.corr && ctx.corr.independent_decimal > b.odds_decimal
      ? `<div class="muted small bet-note">Linked legs: odds cut from ${fmt.oddsDec(ctx.corr.independent_decimal)}</div>` : '';
    const legIcon = { won: '✓', lost: '✗', void: '↺' };
    const legRows = legs ? legs.map((l) =>
      `<div class="bet-leg ${l.result || ''}${l.boost ? ' boosted' : ''}"><span class="leg-icon">${legIcon[l.result] || '•'}</span>` +
      `<span class="leg-desc">${l.boost ? '⚡ ' : ''}${esc(l.description)}</span><span class="muted small">@ ${fmt.oddsDec(l.odds_decimal)}</span></div>`).join('') : '';
    // Bettors can take a bet back only within bet_cancel_minutes of placing it (the server enforces this too).
    const canCancel = Date.now() / 1000 - b.placed_ts < (state.status.bet_cancel_minutes ?? 1) * 60;
    const cancelBtn = isMine(b) && canCancel ? `<button class="btn ghost small cancel-bet" data-id="${b.id}">Cancel</button>`
      : (state.status.auth && state.status.auth.admin_required) ? `<button class="btn ghost small cancel-bet admin" data-id="${b.id}" title="Needs the admin password">Admin cancel</button>`
      : '';
    const fresh = stamp && justPlaced.has(b.id);
    return `<div class="bet-ticket ${b.status}${fresh ? ' just-placed' : ''}">${fresh ? '<span class="placed-stamp" aria-hidden="true">Placed</span>' : ''}
        <div class="bet-ticket-row">
          <div class="bet-desc">${legs ? `<span class="parlay-badge">Parlay ×${legs.length}</span>` : esc(b.description)}</div>
          <span class="status ${b.status}">${b.status}</span>
        </div>
        ${legRows ? `<div class="bet-legs">${legRows}</div>` : ''}
        <div class="bet-ticket-row muted small">
          <span>${fmt.credits(b.stake)} @ ${fmt.oddsDec(b.odds_decimal)}</span>
          <span>${b.status === 'pending' ? `To win ${fmt.credits(b.stake * (b.odds_decimal - 1))}` : `Return ${fmt.credits(b.payout || 0)}`}</span>
        </div>
        ${cut}
        ${perkNote(b)}
        ${b.note ? `<div class="muted small bet-note">${esc(b.note)}</div>` : ''}
        ${b.status === 'pending' && cancelBtn ? `<div class="bet-ticket-row">${cancelBtn}</div>` : ''}
      </div>`;
  }

  function bettorSlips(bets, settled = false, stamp = false) {
    if (!bets.length) return '';
    const groups = new Map();
    bets.forEach((b) => {
      if (!groups.has(b.bettor)) groups.set(b.bettor, []);
      groups.get(b.bettor).push(b);
    });
    const names = [...groups.keys()].sort((a, b) => {
      if (isMine({ bettor: a }) !== isMine({ bettor: b })) return isMine({ bettor: a }) ? -1 : 1;
      return groups.get(b).length - groups.get(a).length || a.localeCompare(b);
    });
    return `<div class="bettor-slips">${names.map((name) => {
      const rows = groups.get(name);
      return `<div class="bet-slip-card ${isMine({ bettor: name }) ? 'me' : ''} ${ticketClass(name)}">
        <div class="bet-slip-head"><span class="swatch s${bettorSlot(name)} lg"></span><b>${nameHtml(name)}</b><span class="muted small right">${betTotals(rows, settled)}</span></div>${ticketExtras(name)}
        ${rows.map((b) => betTicket(b, stamp)).join('')}
      </div>`;
    }).join('')}</div>`;
  }

  // Settled bets show this many games at once, starting from the game picked in the card's dropdown (the most recent
  // by default) and going back. It was 3 before the dropdown existed; set it back to 3 to show three games again.
  const SETTLED_GAMES = 1;

  const signedCredits = (v) => `<span class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${v > 0 ? '+' : v < 0 ? '−' : ''}${fmt.credits(Math.abs(v))}</span>`;
  // "6 bets · 155 wagered", plus the net result once the bets have settled (payouts minus stakes).
  function betTotals(rows, settled) {
    const staked = rows.reduce((a, b) => a + b.stake, 0);
    const net = rows.reduce((a, b) => a + (b.payout || 0) - b.stake, 0);
    return `${rows.length} bet${rows.length === 1 ? '' : 's'} · ${fmt.credits(staked)} wagered${settled ? ` · ${signedCredits(net)}` : ''}`;
  }

  function betsSection() {
    const pending = state.bets.filter((b) => b.status === 'pending');
    return `<section class="card"><div class="section-head"><h2>Open bets</h2>${pending.length ? `<span class="muted small">${betTotals(pending, false)}</span>` : ''}</div>
        ${pending.length ? bettorSlips(pending) : `<p class="muted">No open bets. Bets settle automatically when the next ${stackWord()} game is synced.</p>`}
        <p class="muted small">Settled bets, balances and rankings live on <a href="#bettors">Standings</a>.</p></section>`;
  }

  // The Place bets sidebar's "Your open bets": the signed-in bettor's slip card from the open bets section, where a
  // bet just placed lands with its stamp.
  function myBetsCard() {
    if (!state.me) return '';
    const mine = state.bets.filter((b) => b.status === 'pending' && isMine(b));
    return `<section class="card my-bets"><h2>Your open bets</h2>
        ${mine.length ? bettorSlips(mine, false, true) : '<p class="muted small">Nothing open yet. Bets you place show up here.</p>'}</section>`;
  }

  // Settled bets per game, newest game first: Map(match_id -> bets).
  function settledGames() {
    const groups = new Map();
    state.bets.filter((b) => b.settled_match_id && b.status !== 'pending' && b.status !== 'cancelled').forEach((b) => {
      if (!groups.has(b.settled_match_id)) groups.set(b.settled_match_id, []);
      groups.get(b.settled_match_id).push(b);
    });
    return new Map([...groups].sort(([, a], [, b]) => (b[0].game_started_ts || 0) - (a[0].game_started_ts || 0)));
  }

  // Standings page: settled bets for the game picked in the dropdown (state.settledGame; '' = the most recent), grouped
  // by bettor. The dropdown lists every game with settled bets among the bets loaded.
  function settledSection() {
    const groups = settledGames();
    if (!groups.size) {
      return `<section class="card"><h2>Settled bets</h2><p class="muted">Nothing settled yet. Bets settle when the next ${stackWord()} game is recorded.</p></section>`;
    }
    const keys = [...groups.keys()];
    const picked = keys.includes(state.settledGame) ? state.settledGame : keys[0];
    const label = (rows) => {
      const g = rows[0];
      return `${fmt.date(g.game_started_ts ? g.game_started_ts * 1000 : null)} · ${g.game_map || 'Unknown map'} · ` +
        `${fmt.res(g.game_result)} ${g.game_rounds_won ?? '?'}–${g.game_rounds_lost ?? '?'} · ${rows.length} bet${rows.length === 1 ? '' : 's'}`;
    };
    const options = keys.map((k, i) => `<option value="${esc(i ? k : '')}"${k === picked ? ' selected' : ''}>${esc((i ? '' : 'Latest: ') + label(groups.get(k)))}</option>`).join('');
    return `<section class="card"><div class="section-head"><h2>Settled bets</h2>
        <label class="settled-pick muted small">Game <select id="settled-game">${options}</select></label></div>
        ${gameSlips(keys.slice(keys.indexOf(picked), keys.indexOf(picked) + SETTLED_GAMES).map((k) => groups.get(k)))}</section>`;
  }

  // One block per game: a header with the result and the squad's totals, then each player's bets on it.
  function gameSlips(games) {
    return games.map((rows) => {
      const g = rows[0];
      return `<div class="settled-game">
        <div class="settled-game-head"><span class="chip ${esc(g.game_result || '')}">${fmt.res(g.game_result)}</span>` +
          `<b>${g.game_rounds_won ?? '?'}–${g.game_rounds_lost ?? '?'}</b><span>${esc(g.game_map || 'Unknown map')}</span>` +
          `<span class="muted small">${fmt.date(g.game_started_ts ? g.game_started_ts * 1000 : null)}</span>` +
          `<span class="muted small right">${betTotals(rows, true)}</span></div>
        ${bettorSlips(rows, true)}
      </div>`;
    }).join('');
  }

  function findMarket(id, key) {
    const od = state.odds || {};
    for (const g of ['team', 'player_props', 'top_markets']) {
      for (const mk of od[g] || []) {
        if (mk.market_id === id) return { mk, sel: mk.selections.find((s) => s.key === key) };
      }
    }
    return {};
  }

  function toggleSlip(marketId, selKey) {
    const i = state.slip.findIndex((x) => x.market_id === marketId && x.selection === selKey);
    if (i >= 0) {
      state.slip.splice(i, 1);
    } else {
      const { mk, sel } = findMarket(marketId, selKey);
      if (!mk || !sel) return;
      state.slip = state.slip.filter((x) => x.market_id !== marketId); // one side per market
      state.slip.push({
        market_id: marketId, selection: selKey, selLabel: sel.label,
        desc: mk.type === 'ou' ? `${mk.member} ${mk.stat_label}` : mk.label,
        american: sel.american, decimal: sel.decimal, line: mk.line, stake: state.stake, gameBoost: !!sel.boost,
      });
      window.FiveOnkey?.note('slip', { added: true, desc: `${state.slip[state.slip.length - 1].desc} ${sel.label}`, n: state.slip.length });
    }
    drawSlip();
    syncOddButtons();
  }

  function syncOddButtons() {
    $$('button.odd').forEach((b) => {
      const on = state.slip.some((x) => x.market_id === b.dataset.m && x.selection === b.dataset.s);
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', String(on));
    });
  }

  // ---- custom lines ("I think Loog gets 25 kills") -------------------------------------------------------------
  // state.custom: { puuid, stat, side ('over' = at least N, 'under' = at most N, 'exact' = exactly N), n, quote }. The
  // quote comes from /api/odds/custom, priced like the board's lines; lines too far from a player's usual game aren't
  // offered. Exact numbers are for the counting stats (kills, deaths, assists) only.
  const SIDES = { over: ['At least', 'at_least'], under: ['At most', 'at_most'], exact: ['Exactly', 'exactly'] };
  const isCount = (stat) => ((state.odds && state.odds.stat_defs) || []).some((s) => s.key === stat && s.count);
  const custom = () => (state.custom ||= { puuid: '', stat: 'kills', side: 'over', n: '', quote: null, error: '' });
  const customLine = (c) => (c.n === '' || c.side === 'exact' ? null : c.side === 'over' ? Number(c.n) - 0.5 : Number(c.n) + 0.5);
  let quoteTimer = 0, quoteSeq = 0;

  function customLineCard() {
    const c = custom(), members = state.status.members || [], stats = (state.odds && state.odds.stat_defs) || [];
    if (!members.length || !stats.length) return '';
    if (!c.puuid) c.puuid = members[0].puuid;
    if (c.side === 'exact' && !isCount(c.stat)) c.side = 'over';
    const opt = (v, label, on, off) => `<option value="${esc(v)}"${on ? ' selected' : ''}${off ? ' disabled' : ''}>${esc(label)}</option>`;
    return `<section class="card" id="custom-line"><h2>Custom line</h2>
      ${how('Name your own number: at least, at most, or exactly.', 'Think someone\'s going big (or bad)? Exactly works for kills, deaths and assists. It\'s priced from the same model as the board, and a number too far from a player\'s usual game isn\'t offered.')}
      <div class="ctx-row">
        <label>Player<select id="cl-player">${members.map((m) => opt(m.puuid, m.nickname, m.puuid === c.puuid)).join('')}</select></label>
        <label>Stat<select id="cl-stat">${stats.map((s) => opt(s.key, s.label, s.key === c.stat)).join('')}</select></label>
        <label>Side<select id="cl-side">${Object.entries(SIDES).map(([k, [label]]) => opt(k, label, c.side === k, k === 'exact' && !isCount(c.stat))).join('')}</select></label>
        <label>Number<input id="cl-n" type="number" min="0" step="1" inputmode="numeric" value="${esc(c.n)}" placeholder="${esc(c.quote ? Math.round(c.quote.typical) : '')}"></label>
      </div><div id="cl-quote" class="cl-quote" aria-live="polite">${customQuoteHtml()}</div></section>`;
  }

  function customQuoteHtml() {
    const c = custom(), q = c.quote;
    if (c.error) return `<p class="down small">${esc(c.error)}</p>`;
    if (!q) return '<p class="muted small">Loading…</p>';
    const [word, limitKey] = SIDES[c.side];
    const [lo, hi] = (q.limits && q.limits[limitKey]) || [1, 0];
    const range = lo <= hi ? `${word.toLowerCase()} ${lo} to ${hi}` : 'none right now';
    const hint = `<p class="muted small">${esc(q.member)} usually gets about ${esc(String(Math.round(q.typical)))} ${esc(q.stat_label.toLowerCase())}. Numbers you can pick: ${esc(range)}.</p>`;
    if (c.n === '') return hint;
    const sel = (q.selections || []).find((s) => s.key === c.side);
    if (!sel || !sel.available) return `<p class="down small">${esc((sel && sel.reason) || q.reason || 'Not available.')}</p>${hint}`;
    const what = `${q.member}: ${word.toLowerCase()} ${c.n} ${q.stat_label.toLowerCase()}`;
    const settles = c.side === 'exact' ? `wins only on exactly ${c.n}` : `settles like ${c.side === 'over' ? 'an over' : 'an under'} ${q.line}`;
    return `<div class="cl-offer"><div><b>${esc(what)}</b><div class="muted small">${Math.round(sel.fair_prob * 100)}% chance before the house edge · ${esc(settles)}</div></div>` +
      `<span class="cl-odds">${fmt.odds(sel)}</span><button type="button" class="btn small" id="cl-add">Add to slip</button></div>${hint}`;
  }

  async function fetchCustomQuote() {
    const c = custom(), seq = ++quoteSeq;
    const std = ((state.odds && state.odds.player_props) || []).find((mk) => mk.market_id === `ou:${c.stat}:${c.puuid}`);
    const p = new URLSearchParams({ puuid: c.puuid, stat: c.stat });
    if (c.side === 'exact') {
      p.set('exact', c.n !== '' ? c.n : String(Math.round(std ? std.line : 1))); // before a number is typed: for the range
    } else {
      p.set('line', String(customLine(c) ?? (std ? std.line : 0.5)));
    }
    if (state.ctx.map) p.set('map', state.ctx.map);
    if (Object.keys(state.ctx.agents).length) p.set('agents', JSON.stringify(state.ctx.agents));
    try {
      const res = await api('/api/odds/custom?' + p.toString());
      if (seq !== quoteSeq) return; // a newer request is on its way
      c.quote = res.market; c.error = '';
    } catch (e) {
      if (seq !== quoteSeq) return;
      c.quote = null; c.error = e.message;
    }
    const box = $('#cl-quote');
    if (box) { box.innerHTML = customQuoteHtml(); bindCustomAdd(); }
    const n = $('#cl-n');
    if (n && c.quote) n.placeholder = String(Math.round(c.quote.typical));
  }

  function bindCustomAdd() {
    $('#cl-add')?.addEventListener('click', () => {
      const c = custom(), q = c.quote;
      const sel = q && (q.selections || []).find((s) => s.key === c.side && s.available);
      if (!sel || c.n === '') return;
      state.slip = state.slip.filter((x) => x.market_id !== q.market_id);
      state.slip.push({ market_id: q.market_id, selection: c.side, selLabel: `${SIDES[c.side][0]} ${c.n}`,
        desc: `${q.member} ${q.stat_label} · custom`, american: sel.american, decimal: sel.decimal, line: q.line, stake: state.stake });
      drawSlip();
      toast('Custom line added to the slip');
    });
  }

  function bindCustom(view) {
    if (!$('#custom-line', view)) return;
    const c = custom();
    const requote = (delay) => {
      clearTimeout(quoteTimer);
      quoteSeq++; // anything still on its way is now out of date
      const box = $('#cl-quote');
      if (box && c.quote) box.innerHTML = '<p class="muted small">Checking the odds…</p>';
      quoteTimer = setTimeout(fetchCustomQuote, delay);
    };
    $('#cl-player', view).addEventListener('change', (e) => { c.puuid = e.target.value; requote(0); });
    $('#cl-stat', view).addEventListener('change', (e) => {
      c.stat = e.target.value;
      const side = $('#cl-side', view), exact = side.querySelector('option[value="exact"]');
      exact.disabled = !isCount(c.stat); // exact numbers are for kills, deaths and assists
      if (exact.disabled && c.side === 'exact') { c.side = 'over'; side.value = 'over'; }
      requote(0);
    });
    $('#cl-side', view).addEventListener('change', (e) => { c.side = e.target.value; requote(0); });
    $('#cl-n', view).addEventListener('input', (e) => {
      const v = e.target.value.trim();
      c.n = /^\d{1,4}$/.test(v) ? String(Number(v)) : '';
      requote(250);
    });
    bindCustomAdd();
    requote(0); // the board (map / agents) may have changed since the last quote
  }

  function drawSlip() {
    const slip = $('#slip');
    if (!slip) return;
    slip.innerHTML = slipHtml();
    bindSlip();
  }

  function bindSlip() {
    const slip = $('#slip');
    if (!slip) return;
    $('h2', slip)?.addEventListener('click', () => document.documentElement.classList.toggle('slip-open')); // the phone sheet's handle
    $$('.stake', slip).forEach((inp) => inp.addEventListener('input', (e) => {
      const it = state.slip[Number(e.target.dataset.i)];
      if (!it) return;
      it.stake = Math.max(0, Number(e.target.value) || 0);
      state.stake = it.stake || state.stake;
      localStorage.setItem('fs.stake', String(state.stake));
      const tw = e.target.parentElement.querySelector('.towin');
      if (tw) tw.innerHTML = toWin(it);
      const total = state.slip.reduce((a, x) => a + (Number(x.stake) || 0), 0);
      const tot = $('.slip-total', slip);
      if (tot) tot.textContent = `Total stake ${fmt.credits(total)}`;
      const after = $('.slip-after', slip);
      if (after) after.innerHTML = afterStake(total);
    }));
    $('#parlay-stake')?.addEventListener('input', (e) => {
      const stake = Math.max(0, Number(e.target.value) || 0);
      state.stake = stake || state.stake;
      localStorage.setItem('fs.stake', String(state.stake));
      const tw = $('.parlay-towin', slip);
      if (tw) tw.textContent = parlayToWin(stake);
      const after = $('.slip-after', slip);
      if (after) after.innerHTML = afterStake(stake);
    });
    $$('.mode-btn', slip).forEach((b) => b.addEventListener('click', () => { state.slipMode = b.dataset.mode; drawSlip(); }));
    bindTokens(slip);
    bindRemove(slip);
    $('#clear-slip')?.addEventListener('click', () => { state.slip = []; drawSlip(); syncOddButtons(); window.FiveOnkey?.note('slip', { cleared: true }); });
    $('#place-bets')?.addEventListener('click', placeSlip);
    if ($('#parlay-quote', slip)) fetchParlayQuote();
  }

  function bindTokens(root) {
    $$('.token-btn', root).forEach((b) => b.addEventListener('click', () => {
      const it = state.slip[Number(b.dataset.i)];
      if (it) { it[b.dataset.token] = !it[b.dataset.token]; drawSlip(); }
    }));
  }

  function bindRemove(root) {
    $$('.rm', root).forEach((b) => b.addEventListener('click', () => { state.slip.splice(Number(b.dataset.i), 1); drawSlip(); syncOddButtons(); }));
  }

  // Bets placed in the last moment or two, whose tickets land with a stamp (cleared once it has played).
  const justPlaced = new Set();
  function stampPlaced(ids) {
    ids.forEach((id) => justPlaced.add(id));
    setTimeout(() => ids.forEach((id) => justPlaced.delete(id)), 1800);
  }

  async function placeSlip() {
    if (!state.me) { toast('Sign in as a bettor first', 'bad'); return; }
    if (state.slip.length >= 2 && state.slipMode === 'parlay') {
      const stake = Number($('#parlay-stake')?.value) || state.stake;
      try {
        const res = await api('/api/bets', {
          method: 'POST',
          body: JSON.stringify({ legs: parlayLegs(), stake, context: state.ctx }),
        });
        state.slip = [];
        stampPlaced([res.bet.id]);
        window.FiveOnkey?.note('bet', { parlay: true, legs: (JSON.parse(res.bet.context || '{}').legs || []).length, stake });
        toast(`Parlay placed. It settles after the next ${stackWord()} game.`, 'good');
      } catch (e) {
        toast(e.message, 'bad');
      }
      await loadBets();
      draw();
      return;
    }
    const failures = [];
    const remaining = [];
    const placed = [], placedBets = [];
    for (const it of state.slip) {
      try {
        const tokens = it.boost || it.insurance ? { boost: !!it.boost, insurance: !!it.insurance } : undefined;
        const res = await api('/api/bets', { method: 'POST', body: JSON.stringify({ market_id: it.market_id, selection: it.selection, stake: it.stake, context: state.ctx, tokens }) });
        placed.push(res.bet.id);
        placedBets.push(res.bet);
      } catch (e) {
        failures.push(`${it.desc}: ${e.message}`);
        remaining.push(it);
      }
    }
    state.slip = remaining;
    stampPlaced(placed);
    if (failures.length) toast(failures.join(' · '), 'bad');
    else toast(`Bets placed. They settle after the next ${stackWord()} game.`, 'good');
    if (placedBets.length) {
      const b = placedBets[0];
      window.FiveOnkey?.note('bet', { count: placedBets.length, stake: b.stake, desc: b.description, odds: b.odds_decimal.toFixed(2),
        token: /"(boost_token|insured)"/.test(b.context || '') });
    }
    await loadBets();
    draw();
  }

  // ---- bettors ----------------------------------------------------------------------
  function viewBettors() {
    const bettors = state.bettors || [];
    const start = state.status.starting_balance || 1000;
    if (!bettors.length) {
      return `<div class="card empty"><h2>No bettors yet</h2><p>Go to <a href="#odds">Place bets</a>, type your name in the bet slip and place a pick. Everyone starts with ${fmt.credits(start)} credits.</p></div>`;
    }
    const maxBal = Math.max(1, ...bettors.map((b) => b.balance));
    const bestWin = {};
    state.bets.filter((b) => b.status === 'won').forEach((b) => {
      const net = (b.payout || 0) - b.stake;
      const key = b.bettor.toLowerCase();
      if (!bestWin[key] || net > bestWin[key].net) bestWin[key] = { net, desc: b.description };
    });
    const inPlay = bettors.reduce((a, b) => a + b.balance + (b.pending_stake || 0), 0);
    const rewarded = bettors.reduce((a, b) => a + (b.rewards || 0), 0);
    const given = bettors.reduce((a, b) => a + (b.giveaways || 0), 0);
    const hunted = bettors.reduce((a, b) => a + (b.hunt || 0), 0);
    const issued = bettors.length * start + rewarded + given + hunted;
    const house = issued - inPlay;
    const leader = bettors[0];
    const kpis = [
      kpi('Bettors', bettors.length, `${fmt.credits(start)} credits each to start`),
      kpi('Leader', esc(leader.name), `${fmt.credits(leader.balance)} credits`),
      kpi('Credits in circulation', fmt.credits(inPlay), `${fmt.credits(issued)} issued${rewarded ? `, ${fmt.credits(rewarded)} of it as game rewards` : ''}`),
      kpi('The house is', `<span class="${house > 0 ? 'up' : house < 0 ? 'down' : ''}">${fmt.signed(house, 0)}</span>`, house >= 0 ? 'ahead of the squad' : 'behind the squad'),
    ];
    const me = (state.me ? state.me.name : '').toLowerCase();
    const rows = bettors.map((b, i) => {
      const settled = b.won + b.lost;
      const bw = bestWin[b.name.toLowerCase()];
      const rank = i < 3 ? ['🥇', '🥈', '🥉'][i] : String(i + 1);
      return `<tr class="${b.name.toLowerCase() === me ? 'me' : ''}"><td class="rank">${rank}</td>` +
        `<td><b>${nameHtml(b.name, { link: true })}</b>${b.claimed === false ? ' <span class="muted small">unclaimed</span>' : ''}${bw ? `<div class="muted small">Best win +${fmt.credits(bw.net)} · ${esc(bw.desc)}</div>` : ''}</td>` +
        `<td class="num balance">${fmt.credits(b.balance)}</td>` +
        `<td class="num ${b.debt ? 'down' : 'muted'}" title="${b.debt ? `Owes Onkey's Bank ${fmt.credits(b.debt)}, interest included` : 'No loans'}">${b.debt ? '−' + fmt.credits(b.debt) : '–'}</td>` +
        `<td class="bar-cell"><div class="hbar-track"><div class="hbar-fill" style="width:${Math.round((b.balance / maxBal) * 100)}%"></div></div></td>` +
        `<td class="num ${b.profit > 0 ? 'up' : b.profit < 0 ? 'down' : ''}">${fmt.signed(b.profit, 0)}</td>` +
        `<td class="num">${b.rewards ? '+' + fmt.credits(b.rewards) : '–'}</td>` +
        `<td class="num">${b.transfers ? fmt.signed(b.transfers, 0) : '–'}</td>` +
        `<td class="num ${b.casino > 0 ? 'up' : b.casino < 0 ? 'down' : ''}">${b.casino ? fmt.signed(b.casino, 0) : '–'}</td>` +
        `<td class="num">${b.giveaways ? '+' + fmt.credits(b.giveaways) : '–'}</td>` +
        `<td class="num">${b.hunt ? '+' + fmt.credits(b.hunt) : '–'}</td>` +
        `<td class="num">${b.won}-${b.lost}${b.void ? '-' + b.void : ''}</td>` +
        `<td class="num">${settled ? fmt.pct(b.won / settled) : '–'}</td>` +
        `<td class="num">${b.roi != null ? fmt.signed(b.roi * 100, 0) + '%' : '–'}</td>` +
        `<td class="num">${b.pending}${b.pending_stake ? ` <span class="muted small">(${fmt.credits(b.pending_stake)})</span>` : ''}</td></tr>`;
    }).join('');
    const vizHelpers = { esc, fmt, slot: (puuid) => memberIndex().get(puuid)?.slot, bettorSlot };
    // Laid out like Place bets: the main cards on the left, Send credits and Game rewards in a narrow sidebar.
    return `<section class="kpis">${kpis.join('')}</section>
      <div class="odds-layout bettors-layout"><div>
      <section class="card"><h2>Rankings</h2>${how('Ordered by credits minus bank debt.', `Profit and ROI cover match bets only. Profit counts open stakes and is measured against the ${fmt.credits(start)} everyone started with, leaving out game rewards, transfers, bank loans, casino results, what the house gave back and the Banana Hunt (shown separately). Transfers is what they received minus what they sent, generosity tax included. Casino is payouts minus stakes this season across the casino games. House is what the house gave them this season: secret objectives met, bad beats refunded and daily wheel prizes.`)}
        <div class="table-wrap"><table class="rankings"><thead><tr><th class="rank">#</th><th>Bettor</th><th class="num">Credits</th><th class="num" title="What they still owe Onkey's Bank, interest included">Debt</th><th></th><th class="num">Profit</th><th class="num">Rewards</th><th class="num" title="Credits received from other bettors minus credits sent, generosity tax included">Transfers</th><th class="num" title="Casino payouts minus stakes this season">Casino</th><th class="num" title="Credits from the house this season: secret objectives met, bad beats refunded and daily wheel prizes">House</th><th class="num" title="Credits picked in the Banana Hunt this season">Hunt</th><th class="num">W-L-void</th><th class="num">Win %</th><th class="num">ROI</th><th class="num">Open</th></tr></thead><tbody>${rows}</tbody></table></div>
        ${resetPanel()}</section>
      ${state.bettingReport ? window.FiveViz.bettingReport(state.bettingReport, vizHelpers) : ''}
      ${settledSection()}
      ${pastSeasonsCard()}
      ${state.bettingReport ? window.FiveViz.oddsAccuracy(state.bettingReport, vizHelpers) : ''}
      </div><aside class="odds-side bettors-side">${offerCard()}${houseCard()}${bankCard()}${transferCard()}${rewardsCard()}</aside></div>`;
  }

  // Onkey's Bank (bank.py, /api/bank): borrow up to the limit at interest and pay it back any time, oldest loan
  // first. Borrowing takes a second click on a confirm line that spells out what you'll owe, like Send credits.
  function bankCard() {
    const me = state.me, bank = state.bank || {}, terms = bank.bank || { max: 1000, interest: 0.1, min: 1, enabled: true };
    const st = bank.me, l = state.loan;
    if (!terms.enabled) return '';
    const pct = fmt.pct(terms.interest);
    let body;
    if (!me) body = '<p class="muted small"><a href="#" data-signin>Sign in</a> to borrow credits.</p>';
    else if (!st) body = '<p class="muted small">Loading…</p>';
    else {
      const amount = Number(l.amount), owe = amount + Math.round(amount * terms.interest);
      const canPay = Math.floor(Math.min(st.debt, me.balance));
      const confirm = l.confirm && amount >= 1
        ? `<div class="transfer-confirm" role="group" aria-label="Confirm loan">Borrow <b>${fmt.credits(amount)}</b> credits? You'll owe <b>${fmt.credits(owe)}</b> (${pct} interest)` +
          `${st.debt ? `, on top of the ${fmt.credits(st.debt)} you already owe` : ''}.` +
          '<div class="btn-row"><button class="btn primary small" id="loan-go">Borrow</button><button class="btn ghost small" id="loan-cancel">Cancel</button></div></div>'
        : '';
      // In debt, the scientist is waiting: the plan in the lore is to make Onkey's owner desperate enough to sell.
      const lurk = st.debt ? window.sciNote('Zere is an easier way to pay zis back. You know my number.') : '';
      const standing = st.debt
        ? `<p class="bank-standing">You owe <b class="down">${fmt.credits(st.debt)}</b> on ${st.open.length} loan${st.open.length === 1 ? '' : 's'} (${fmt.credits(st.borrowed)} borrowed). ` +
          `${st.room ? `You can borrow ${fmt.credits(st.room)} more.` : 'Pay it all back, interest included, before borrowing again.'}</p>`
        : `<p class="bank-standing">You owe nothing. You can borrow up to <b>${fmt.credits(st.room)}</b> credits.</p>`;
      const borrow = st.room ? `<div class="transfer-form bank-form">
          <label>Borrow<input id="loan-amount" type="number" min="1" max="${st.room}" step="1" inputmode="numeric" value="${esc(l.amount)}" placeholder="${Math.min(100, st.room)}"></label>
          <button class="btn small" id="loan-ask"${l.confirm ? ' disabled' : ''}>Borrow…</button>
        </div>${confirm}` : '';
      const repay = st.debt ? `<div class="transfer-form bank-form">
          <label>Pay back<input id="loan-repay" type="number" min="1" max="${canPay}" step="1" inputmode="numeric" value="${esc(l.repay)}" placeholder="${canPay}"></label>
          <button class="btn small" id="loan-pay">Pay</button>
          <button class="btn ghost small" id="loan-pay-all"${canPay < 1 ? ' disabled' : ''}>Pay ${canPay >= st.debt ? 'it all' : 'all you can'}</button>
        </div>` : '';
      const open = st.open.map((x) => `<li class="recent-row loan-row"><span><b>${fmt.credits(x.principal)}</b> borrowed ${fmt.date(x.taken_ts * 1000)}</span>` +
        `<b class="num down">${fmt.credits(x.due)} due</b></li>`).join('');
      const cleared = st.cleared.slice(0, 3).map((x) => `<li class="recent-row loan-row"><span><b>${fmt.credits(x.principal)}</b> borrowed ${fmt.date(x.taken_ts * 1000)}</span>` +
        `<span class="num muted small">paid back ${fmt.credits(x.owed)}</span></li>`).join('');
      body = `${standing}${lurk}${borrow}${repay}${open ? `<h3 class="small">Open loans</h3><ul class="recent">${open}</ul>` : ''}` +
        `${cleared ? `<h3 class="small">Paid back</h3><ul class="recent">${cleared}</ul>` : ''}`;
    }
    return `<section class="card" id="bank"><h2>Onkey's Bank</h2>
      ${how(`Borrow up to ${fmt.credits(terms.max)} credits at ${pct} interest. Pay it all back before you can borrow the full amount again.`,
      `Borrow any whole amount, as long as what you have out on loan stays within ${fmt.credits(terms.max)}: once the full ${fmt.credits(terms.max)} is out, the bank lends nothing more until every loan is paid back, interest included. ` +
      'Borrowed credits land on your balance and can be bet like any other. Pay back any whole amount at a time; the oldest loan is paid first. ' +
      'Loans don\'t count toward betting profit, ROI or record: the Rankings show what you owe in their own column and rank by credits minus debt. A season reset forgives every debt along with the balances.')}
      ${body}</section>`;
  }

  function bindBank(view) {
    const l = state.loan;
    const edited = () => { // a change after "Borrow…" takes the confirm line away: it would be out of date
      if (!l.confirm) return;
      l.confirm = false;
      $('#bank .transfer-confirm', view)?.remove();
      const ask = $('#loan-ask', view);
      if (ask) ask.disabled = false;
    };
    $('#loan-amount', view)?.addEventListener('input', (e) => { l.amount = e.target.value; edited(); });
    $('#loan-repay', view)?.addEventListener('input', (e) => { l.repay = e.target.value; });
    $('#loan-ask', view)?.addEventListener('click', () => {
      const amount = Number(l.amount), room = (state.bank && state.bank.me ? state.bank.me.room : 0) || 0;
      if (!(amount >= 1)) { toast('Enter how much to borrow', 'bad'); return; }
      if (amount > room) { toast(`The bank will lend you ${fmt.credits(room)} more`, 'bad'); return; }
      l.confirm = true;
      draw();
      $('#loan-go')?.focus();
    });
    $('#loan-cancel', view)?.addEventListener('click', () => { l.confirm = false; draw(); });
    $('#loan-go', view)?.addEventListener('click', async (e) => {
      e.currentTarget.disabled = true;
      try {
        const r = await api('/api/bank/borrow', { method: 'POST', body: JSON.stringify({ amount: Number(l.amount) }) });
        toast(`Borrowed ${fmt.credits(r.loan.principal)} credits. You owe ${fmt.credits(r.loan.owed)}.`, 'good');
        window.FiveOnkey?.note('scientist', { kind: 'bank' }); // debt is his plan: he calls
        state.loan = { amount: '', repay: '', confirm: false };
      } catch (err) {
        toast(err.message, 'bad');
        l.confirm = false;
      }
      await loadBets();
      draw();
    });
    const pay = async (amount) => {
      if (!(amount >= 1)) { toast('Enter how much to pay back', 'bad'); return; }
      try {
        const r = await api('/api/bank/repay', { method: 'POST', body: JSON.stringify({ amount }) });
        const n = r.repaid.cleared.length;
        toast(`Paid back ${fmt.credits(r.repaid.paid)} credits${n ? `, ${n === 1 ? 'a loan' : n + ' loans'} cleared` : ''}`, 'good');
        state.loan = { amount: '', repay: '', confirm: false };
      } catch (err) {
        toast(err.message, 'bad');
      }
      await loadBets();
      draw();
    };
    $('#loan-pay', view)?.addEventListener('click', () => pay(Number(l.repay)));
    $('#loan-pay-all', view)?.addEventListener('click', () =>
      pay(Math.floor(Math.min(state.bank && state.bank.me ? state.bank.me.debt : 0, state.me ? state.me.balance : 0))));
  }

  // The generosity tax (bets.py TAX_RATE / TAX_MIN_TRANSFER, sent in /api/status): send someone enough credits and
  // you take a cut of the net winnings of their next winning bet.
  const taxPct = () => fmt.pct(state.status.tax_rate ?? 0.1);
  const taxMin = () => state.status.tax_min_transfer ?? 250;
  const openTax = (sender, recipient) => (state.transfers || []).some((x) => x.tax_status === 'open' &&
    x.sender.toLowerCase() === sender.toLowerCase() && x.recipient.toLowerCase() === recipient.toLowerCase());

  // What a transfer row says about its tax: waiting for the recipient's next win, or collected (from which bet).
  function taxLine(x) {
    if (x.tax_status === 'open') return `<span class="tax-chip open">${taxPct()} tax on ${esc(x.recipient)}'s next win</span>`;
    if (x.tax_status !== 'paid') return '';
    const from = x.tax_bet_net != null ? ` (${taxPct()} of +${fmt.credits(x.tax_bet_net)}${x.tax_bet_description ? ` on “${esc(x.tax_bet_description)}”` : ''})` : '';
    return `<span class="tax-chip paid">${esc(x.sender)} collected +${fmt.credits(x.tax_amount)}${from}</span>`;
  }

  // Send credits to another bettor (paying off a side bet, spotting a friend), then this season's transfers. Sending
  // takes a second click on a confirm line that spells out who gets how much: there's no undo.
  function transferCard() {
    const me = state.me, t = state.transfer, list = state.transfers || [];
    const others = (state.bettors || []).filter((b) => !me || b.name.toLowerCase() !== me.name.toLowerCase())
      .sort((a, b) => a.name.localeCompare(b.name));
    let form;
    if (!me) form = '<p class="muted small"><a href="#" data-signin>Sign in</a> to send credits.</p>';
    else if (!others.length) form = '<p class="muted small">Nobody else to send credits to yet.</p>';
    else {
      const amount = Number(t.amount);
      const tax = !t.to ? ''
        : openTax(me.name, t.to) ? `You already have a tax waiting on ${esc(t.to)}'s next win, so this one doesn't add another.`
        : amount >= taxMin() ? `You'll collect the generosity tax: ${taxPct()} of the winnings on ${esc(t.to)}'s next winning bet.`
        : `Send ${fmt.credits(taxMin())} or more to earn the generosity tax on ${esc(t.to)}'s next win.`;
      const confirm = t.confirm && t.to && amount >= 1
        ? `<div class="transfer-confirm" role="group" aria-label="Confirm transfer">Send <b>${fmt.credits(amount)}</b> credits to <b>${esc(t.to)}</b>? ` +
          `You'll have ${fmt.credits(me.balance - amount)} left, and it can't be undone.<div class="small tax-note">${tax}</div>` +
          '<div class="btn-row"><button class="btn primary small" id="tr-go">Send</button><button class="btn ghost small" id="tr-cancel">Cancel</button></div></div>'
        : '';
      form = `<div class="transfer-form">
          <label>To<select id="tr-to"><option value="">Pick a bettor</option>${others.map((b) =>
            `<option value="${esc(b.name)}"${t.to === b.name ? ' selected' : ''}>${esc(b.name)}${b.claimed === false ? ' (unclaimed)' : ''}</option>`).join('')}</select></label>
          <label>Amount<input id="tr-amount" type="number" min="1" step="1" inputmode="numeric" value="${esc(t.amount)}" placeholder="50"></label>
          <label class="grow"><span>Note <span class="muted small">(optional)</span></span><input id="tr-note" maxlength="80" value="${esc(t.note)}" placeholder="What it's for"></label>
          <button class="btn small" id="tr-send"${t.confirm ? ' disabled' : ''}>Send…</button>
        </div>${confirm}
        <p class="muted small">You have ${fmt.credits(me.balance)} credits.</p>`;
    }
    const rows = list.slice(0, 12).map((x) =>
      `<li class="recent-row transfer-row"><span><span class="swatch s${bettorSlot(x.sender)}"></span><b>${esc(x.sender)}</b> → ` +
      `<span class="swatch s${bettorSlot(x.recipient)}"></span><b>${esc(x.recipient)}</b></span><b class="num">${fmt.credits(x.amount)}</b>` +
      `<span class="muted transfer-note">${x.note ? esc(x.note) : ''}</span><span class="muted small">${fmt.date(x.created_ts * 1000)}</span>` +
      `${taxLine(x) ? `<span class="transfer-tax">${taxLine(x)}</span>` : ''}</li>`).join('');
    return `<section class="card" id="transfers"><h2>Send credits</h2>
      <div class="tax-banner"><div><b>The generous monkey gets rewarded.</b> ` +
      `Send someone ${fmt.credits(taxMin())}+ credits and you collect the <b>generosity tax</b>: ${taxPct()} of the winnings on their next winning bet.</div></div>
      ${how('Pay off a side bet, spot a friend, and get a cut of their next win.',
      `Credits move straight from your balance to theirs. The tax is paid by the person you sent to, out of their next winning bet: ${taxPct()} of what it won ` +
      `(payout minus stake), and if several of their bets win on the same game, the biggest winner is the one taxed. Only transfers of ${fmt.credits(taxMin())} or more earn it, ` +
      'and you can only have one tax waiting on each person, so sending again before it\'s paid doesn\'t stack. Their bet ticket shows where the tax went. ' +
      'Transfers and taxes don\'t count toward anyone\'s betting profit, ROI or record: the Rankings show them in their own column. A season reset archives them with everything else, and a tax still waiting then is dropped.')}
      ${form}
      ${rows ? `<h3 class="small">This season</h3><ul class="recent">${rows}</ul>` : ''}</section>`;
  }

  function bindTransfers(view) {
    if (!$('#tr-to', view)) return;
    const t = state.transfer;
    const edited = () => { // a change after "Send…" takes the confirm line away: it would be out of date
      if (!t.confirm) return;
      t.confirm = false;
      $('.transfer-confirm', view)?.remove();
      $('#tr-send', view).disabled = false;
    };
    $('#tr-to', view).addEventListener('change', (e) => { t.to = e.target.value; edited(); });
    $('#tr-amount', view).addEventListener('input', (e) => { t.amount = e.target.value; edited(); });
    $('#tr-note', view).addEventListener('input', (e) => { t.note = e.target.value; edited(); });
    $('#tr-send', view).addEventListener('click', () => {
      const amount = Number(t.amount);
      if (!t.to) { toast('Pick who to send credits to', 'bad'); return; }
      if (!(amount >= 1)) { toast('The smallest transfer is 1 credit', 'bad'); return; }
      if (state.me && amount > state.me.balance) { toast(`You only have ${fmt.credits(state.me.balance)} credits`, 'bad'); return; }
      t.confirm = true;
      draw();
      $('#tr-go')?.focus();
    });
    $('#tr-cancel', view)?.addEventListener('click', () => { t.confirm = false; draw(); });
    $('#tr-go', view)?.addEventListener('click', async (e) => {
      e.currentTarget.disabled = true;
      try {
        const r = await api('/api/transfers', { method: 'POST', body: JSON.stringify({ to: t.to, amount: Number(t.amount), note: t.note }) });
        window.FiveOnkey?.note('transfer', { amount: r.transfer.amount, to: r.transfer.recipient });
        toast(`Sent ${fmt.credits(r.transfer.amount)} credits to ${r.transfer.recipient}` +
          (r.transfer.tax_status === 'open' ? `. You'll collect ${taxPct()} of their next win.` : ''), 'good');
        state.transfer = { to: '', amount: '', note: '', confirm: false };
      } catch (err) {
        toast(err.message, 'bad');
        t.confirm = false;
      }
      await loadBets();
      draw();
    });
  }

  // Ending the season is typed-confirmation only, and says exactly what happens (the server checks too).
  function resetPanel() {
    if (!state.resetOpen) return '<div class="btn-row"><button class="btn ghost small" id="reset-bets">Reset season…</button></div>';
    const cur = state.seasons?.current || {};
    const start = fmt.credits(state.status.starting_balance || 1000);
    const next = `Season ${(state.seasons?.seasons?.length || 0) + 1}`;
    const admin = state.status.auth && state.status.auth.admin_required;
    return `<div class="reset-panel" role="group" aria-labelledby="reset-title">
      <h3 id="reset-title">End the season?</h3>
      <p>This season's final standings, ${fmt.n0(cur.bets || 0)} bet${cur.bets === 1 ? '' : 's'}, ${fmt.n0(cur.rewards || 0)} game reward${cur.rewards === 1 ? '' : 's'} and ${fmt.n0(cur.transfers || 0)} transfer${cur.transfers === 1 ? '' : 's'} are saved as <b>${esc(next)}</b> under Past seasons.
        Casino history is also kept with this season. Then every bettor goes back to ${start} credits, open bets are closed, and a new season starts. Accounts and passwords stay.</p>
      <label><span>Type <b>RESET</b> to confirm</span><input id="reset-confirm-text" autocomplete="off" spellcheck="false"></label>
      ${admin ? '<label><span>Admin password</span><input id="reset-admin" type="password" autocomplete="off"></label>' : ''}
      <div class="btn-row"><button class="btn danger small" id="reset-go" disabled>End season</button><button class="btn ghost small" id="reset-cancel">Cancel</button></div>
    </div>`;
  }

  function pastSeasonsCard() {
    const seasons = state.seasons?.seasons || [];
    if (!seasons.length) return '';
    const rows = seasons.map((s) => {
      const st = s.standings || [];
      const champ = st[0];
      const table = `<div class="table-wrap"><table class="compact"><thead><tr><th>#</th><th>Bettor</th><th class="num">Final credits</th>` +
        '<th class="num">Betting profit</th><th class="num">Rewards</th><th class="num">Casino</th><th class="num">W-L</th></tr></thead><tbody>' +
        st.map((b, i) => `<tr><td>${i + 1}</td><td><span class="swatch s${bettorSlot(b.name)}"></span>${esc(b.name)}</td>` +
          `<td class="num">${fmt.credits(b.balance)}</td><td class="num ${b.profit > 0 ? 'up' : b.profit < 0 ? 'down' : ''}">${fmt.signed(b.profit, 0)}</td>` +
          `<td class="num">${b.rewards ? '+' + fmt.credits(b.rewards) : '–'}</td><td class="num">${(b.casino ?? b.slots) ? fmt.signed(b.casino ?? b.slots, 0) : '–'}</td><td class="num">${b.won}-${b.lost}</td></tr>`).join('') +
        '</tbody></table></div>';
      const dates = `${s.started_ts ? fmt.date(s.started_ts * 1000) : '?'} – ${fmt.date(s.ended_ts * 1000)}`;
      return `<details class="season"><summary><b>${esc(s.name)}</b><span class="muted small">${esc(dates)}</span>` +
        `${champ ? `<span>🏆 ${esc(champ.name)} · ${fmt.credits(champ.balance)}</span>` : ''}<span class="muted small right">${fmt.n0(s.bets)} bets</span></summary>${table}</details>`;
    }).join('');
    return `<section class="card"><h2>Past seasons</h2><p class="muted small">Every reset saves the season here. Click one for its final standings.</p>${rows}</section>`;
  }

  const REWARD_GAMES = 5; // the Game rewards card shows the last this many games

  // The house (/api/house, fivestack/house.py): the pot its edge has built from bets and slots, the jackpot, the next
  // game's secret objectives (only how many and their prizes), the last game's objectives revealed, and the latest
  // giveaways.
  // The scientist's offer for Onkey (app.py: scientist_offer, to a bettor who's nearly broke). It can only be refused.
  function offerCard() {
    if (!state.me || !state.me.scientist_offer) return '';
    return `<section class="card"><h2>A call for you</h2>${window.sciNote('Twenty-five zousand for ze monkey. You look like you need it.',
      '<button class="btn small" id="sci-refuse">Onkey is not for sale</button>')}</section>`;
  }
  function bindOffer(view) {
    $('#sci-refuse', view)?.addEventListener('click', async (e) => {
      e.currentTarget.disabled = true;
      try {
        const r = await api('/api/scientist/refuse', { method: 'POST', body: '{}' });
        state.me.scientist_offer = false;
        toast(`You turned him down. You earned the title "${r.item.name}": wear it from Onkey's Shop.`, 'good');
        window.FiveOnkey?.note('sci_refused');
      } catch (err) { toast(err.message, 'bad'); }
      draw();
    });
  }

  const sciHouse = Math.random() < 0.15; // decided once per visit, so the note doesn't flicker between redraws
  function houseCard() {
    const h = state.house;
    if (!h) return '';
    const r = h.rules, credits = (v) => fmt.credits(Math.max(0, v));
    const rule = `The house keeps a small edge on every bet and slot spin. ${fmt.pct(1 - r.jackpot_share)} of what that edge has taken ` +
      `fills the pot and ${fmt.pct(r.jackpot_share)} builds the jackpot, which the daily wheel's rarest slice pays out. Before each game the house hides three objectives: a personal goal ` +
      `for one squad member, set from their own past games so it's as reachable for them as for anyone (and the squad's lower scorers are ` +
      'picked more often), a goal the whole squad shares, and a goal any bettor can meet. Each is worth a share of the pot, more for a ' +
      `rarer one, split evenly between everyone who meets it, and they're revealed once the game is recorded. A lost bet that only just ` +
      `missed (an over / under by one, a parlay of 3 or more legs one leg short, a match result lost in overtime) gets ${fmt.pct(r.refund_rate)} ` +
      `of its stake back, up to ${fmt.credits(r.refund_max)} credits. A surrender carries the objectives over to the next game.`;
    const next = h.next.objectives
      ? `<p class="house-next"><b>${h.next.objectives} secret objectives</b> for the next game, worth ${h.next.prizes.map((p) => fmt.credits(p)).join(', ')} credits.</p>`
      : '<p class="muted small">No objectives yet: they start once the pot can pay for them.</p>';
    const mark = { met: '✓', missed: '✗', void: '–' };
    const last = h.last.length ? `<h3>Last game's objectives</h3><ul class="house-goals">${h.last.map((o) => {
      const won = Object.entries(o.winners).filter(([, v]) => v > 0);
      const who = o.status === 'void' ? 'No result: the game couldn\'t tell'
        : won.length ? won.map(([n, v]) => `${plainName(n)} +${fmt.credits(v)}`).join(', ') : 'Nobody';
      return `<li class="${esc(o.status)}"><span class="house-mark" aria-label="${esc(o.status)}">${mark[o.status] || ''}</span>` +
        `<div><div>${esc(o.text)} <span class="muted small">${fmt.credits(o.prize)}</span></div><div class="muted small">${who}</div></div></li>`;
    }).join('')}</ul>` : '';
    const recent = h.recent.length ? `<h3>Latest giveaways</h3><ul class="house-recent">${h.recent.map((g) =>
      `<li><span>${plainName(g.bettor)}</span><span class="muted small">${esc(g.note || '')}</span><span class="num up">+${fmt.credits(g.amount)}</span></li>`).join('')}</ul>` : '';
    // Now and then the scientist has a word about the house's take (`sciHouse`; see docs/onkey-lore.md).
    const sci = sciHouse ? window.sciNote('Every credit ze house takes is one you cannot spend on bananas. Zis pleases me.') : '';
    return `<section class="card house-card"><h2>The house</h2>${how('Some of what the house takes goes back to you.', rule)}${sci}` +
      `<div class="house-pots"><div><span class="tile-label">Pot</span><b>${credits(h.pot)}</b></div>` +
      `<div><span class="tile-label">Jackpot</span><b>${credits(h.jackpot)}</b><a class="muted small" href="#wheel">Win it on the daily wheel</a></div></div>` +
      `${next}${last}${recent}</section>`;
  }

  function rewardsCard() {
    const s = state.status, game = s.game_reward || 0, win = s.win_reward || 0, bonus = s.performance_bonus_max || 0;
    if (!game && !win && !bonus) return '';
    const rule = `Every squad member earns ${fmt.credits(game)} credits for each ${stackWord()} game${win ? ` (${fmt.credits(game + win)} for a win)` : ''}, ` +
      `plus a performance bonus of up to ${fmt.credits(bonus)} based on how their ACS compares with their own previous ${stackWord()} games: ` +
      'beat 80% of them and the bonus is 80%, rounded to the nearest 5. With fewer than 5 of those games to compare against, the bonus is half.';
    const byGame = new Map();
    (state.rewards || []).forEach((r) => {
      if (!byGame.has(r.match_id)) byGame.set(r.match_id, []);
      byGame.get(r.match_id).push(r);
    });
    const games = [...byGame.values()].slice(0, REWARD_GAMES).map((rs) => {
      const g = rs[0], won = g.result === 'win';
      const people = rs.map((r) => {
        const why = r.beat_share == null ? `fewer than 5 earlier ${stackWord()} games` : `beat ${fmt.pct(r.beat_share)} of their earlier ${stackWord()} games`;
        return `<span class="reward" title="${esc(`${r.nickname || r.bettor}: ACS ${fmt.n0(r.acs)}, ${why}`)}"><b>${esc(r.bettor)}</b><span>+${fmt.credits(r.base + r.bonus)}</span></span>`;
      }).join('');
      return `<li class="recent-row reward-row"><span class="chip ${won ? 'win' : 'loss'}">${won ? 'W' : 'L'}</span>` +
        `<span class="recent-score">${g.rounds_won ?? '?'}–${g.rounds_lost ?? '?'}</span><span>${esc(g.map || '')}</span>` +
        `<span class="rewards-list">${people}</span><span class="muted small">${fmt.date(g.started_ts ? g.started_ts * 1000 : null)}</span></li>`;
    }).join('');
    return `<section class="card rewards-card"><h2>Game rewards</h2>${how(`${fmt.credits(game)} a game each, plus up to ${fmt.credits(bonus)} for playing well.`, `${rule} Hover a name for the details.`)}` +
      (games ? `<ul class="recent">${games}</ul>` : `<p class="muted">No rewards yet. They are paid when the next ${stackWord()} game is recorded.</p>`) + '</section>';
  }

  // ---- events -------------------------------------------------------------------
  // Everything betting-related that needs wiring after a page is drawn (called from app.js's bind()).
  function bind(view) {
    $$('button.odd', view).forEach((b) => b.addEventListener('click', () => toggleSlip(b.dataset.m, b.dataset.s)));
    $('#settled-game', view)?.addEventListener('change', (e) => { state.settledGame = e.target.value; draw(); });
    bindSlip();
    bindCustom(view);
    bindTransfers(view);
    bindBank(view);
    bindOffer(view);
    $$('.cancel-bet', view).forEach((b) => b.addEventListener('click', async () => {
      const headers = {};
      if (b.classList.contains('admin')) {
        const pw = window.prompt('Admin password');
        if (pw == null) return;
        headers['X-Admin-Password'] = pw;
      }
      try { await api('/api/bets/' + b.dataset.id, { method: 'DELETE', headers }); toast('Bet cancelled, stake refunded'); await loadBets(); draw(); }
      catch (e) { toast(e.message, 'bad'); }
    }));
    $('#reset-bets')?.addEventListener('click', () => { state.resetOpen = true; draw(); $('#reset-confirm-text')?.focus(); });
    $('#reset-cancel')?.addEventListener('click', () => { state.resetOpen = false; draw(); });
    $('#reset-confirm-text')?.addEventListener('input', (e) => {
      $('#reset-go').disabled = e.target.value.trim().toUpperCase() !== 'RESET';
    });
    $('#reset-go')?.addEventListener('click', async (e) => {
      e.currentTarget.disabled = true;
      const headers = {};
      const pw = $('#reset-admin');
      if (pw) headers['X-Admin-Password'] = pw.value;
      try {
        const r = await api('/api/bettors/reset', { method: 'POST', body: JSON.stringify({ confirm: $('#reset-confirm-text').value }), headers });
        toast(`${r.season.name} saved to Past seasons. New season started.`, 'good');
        state.resetOpen = false;
        await Promise.all([loadBets(), loadBettingReport(), loadSeasons()]);
        draw();
      } catch (err) {
        toast(err.message, 'bad');
        e.currentTarget.disabled = false;
      }
    });
  }

  return { init, bind, loadBets, loadBettingReport, loadSeasons, betsSection, slipHtml, viewBettors, customLineCard, myBetsCard };
})();
