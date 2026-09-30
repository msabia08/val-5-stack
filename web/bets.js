/* 5-Stack Tracker: the betting UI. The bet slip, bet tickets, the Bettors tab (rankings, report card, settled bets,
 * rewards, seasons) and their data loading, split out of app.js the way the charts live in viz.js.
 *
 * app.js calls FiveBets.init() once with the helpers this needs (state, api, draw, ...), then uses the functions
 * returned at the bottom. Loaded before app.js; plain JS, no dependencies.
 */
window.FiveBets = (() => {
  'use strict';

  // Shared with app.js (set by init): the page state object and its helpers.
  let state, $, $$, api, bettorSlot, draw, esc, fmt, kpi, memberIndex, toast;
  // A one-line explanation with the rest folded behind "How this works" (the full text is still one click away).
  const how = (summary, more) => `<details class="how"><summary>${summary}</summary><div class="how-body">${more}</div></details>`;

  function init(ctx) {
    ({ state, $, $$, api, bettorSlot, draw, esc, fmt, kpi, memberIndex, toast } = ctx);
  }

  // ---- data -------------------------------------------------------------------
  const loadBettingReport = async () => { state.bettingReport = await api('/api/betting-report'); };
  const loadSeasons = async () => { state.seasons = await api('/api/seasons'); };
  const loadBets = async () => {
    const [b, l, m, r] = await Promise.all([api('/api/bets?limit=600'), api('/api/bettors'), api('/api/bettor/me'), api('/api/rewards?limit=60')]);
    state.bets = b.bets; state.bettors = l.bettors; state.me = m.bettor || null; state.rewards = r.rewards;
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
  const parlayKey = () => JSON.stringify([state.slip.map((x) => [x.market_id, x.selection]), state.ctx]);
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
    const legs = state.slip.map((x, i) =>
      `<div class="slip-item parlay-leg${linked.has(i) ? ' linked' : ''}"><div><div class="slip-desc">${esc(x.desc)}</div>` +
      `<div class="muted small">${esc(x.selLabel)} @ ${esc(x.american)}${linked.has(i) ? ' · <span class="linked-tag">linked</span>' : ''}</div></div>` +
      `<button class="btn ghost icon rm" data-i="${i}" aria-label="Remove">✕</button></div>`).join('');
    const dec = parlayDecimal();
    let note = '';
    if (!pq) note = '<p class="muted small">Checking how these legs go together…</p>';
    else if (pq.error) note = `<p class="small down parlay-blocked">${esc(pq.error)}</p>`;
    else if (q.odds_decimal < q.independent_decimal) {
      note = how(`Linked legs: odds cut from ${fmt.oddsDec(q.independent_decimal)} to ${fmt.oddsDec(q.odds_decimal)}.`,
        `The legs marked "linked" won together more often than chance over the last ${q.games} games at today's lines ` +
        `(a player's kills and ACS, say), so multiplying their odds would overpay. The price is cut by how much more often ` +
        `they all landed together than they would have if they were unrelated, pulled toward "unrelated" when there are few ` +
        `games. Odds are only ever cut, never raised, and a parlay never pays less than its longest leg.`);
    }
    return `${legs}<div class="parlay-summary"><span>${state.slip.length}-leg parlay</span>` +
      `<b>${pq && pq.error ? '–' : fmt.oddsDec(dec)}</b></div>${note}`;
  }

  async function fetchParlayQuote() {
    const key = parlayKey();
    if (currentQuote() || parlayAsking === key) return;
    const seq = ++parlaySeq;
    parlayAsking = key;
    let next;
    try {
      const quote = await api('/api/odds/parlay', { method: 'POST', body: JSON.stringify({
        legs: state.slip.map((x) => ({ market_id: x.market_id, selection: x.selection })), context: state.ctx }) });
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
    const tw = $('.parlay-towin');
    if (tw) tw.textContent = parlayToWin(Number($('#parlay-stake')?.value) || 0);
    const place = $('#place-bets');
    if (place && state.me) place.disabled = !!next.error;
  }

  function slipHtml() {
    const me = state.me;
    const account = me
      ? `<div class="account"><div>Betting as <b>${esc(me.name)}</b></div><div class="acct-balance"><b>${fmt.credits(me.balance)}</b> credits</div>` +
        (me.open_bets ? `<div class="muted small">+${fmt.credits(me.open_stake)} on ${me.open_bets} open bet${me.open_bets === 1 ? '' : 's'}</div>` : '') +
        `<div class="btn-row"><button class="btn ghost small" id="bettor-signout">Sign out</button><button class="btn ghost small" id="bettor-password">Change password</button></div></div>`
      : `<div class="account"><label>Name<input id="bettor-name" placeholder="Your name" value="${esc(state.bettor)}" autocomplete="username" maxlength="32"></label>` +
        `<label>Betting password<input id="bettor-pass" type="password" placeholder="Yours alone, not the site password" autocomplete="current-password"></label>` +
        `<div class="btn-row"><button class="btn small" id="bettor-signin">Sign in</button><button class="btn ghost small" id="bettor-register">Create account</button></div>` +
        `<p class="muted small">Each bettor has a personal password, so nobody can bet or cancel under your name. New accounts start with ${fmt.credits(state.status.starting_balance)} credits.</p></div>`;
    if (!state.slip.length) {
      return `<h2>Bet slip</h2>${account}<p class="muted">Tap any odds to add a pick.</p><p class="muted small">Picks placed up to ${fmt.n0(state.status.bet_grace_minutes ?? 2)} min after a game starts still count for it. You can cancel a pick for ${fmt.n0(state.status.bet_cancel_minutes ?? 1)} min after placing it.</p>`;
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
        <p class="muted small">All ${state.slip.length} legs must win. If one is voided (a push), the payout uses the odds of the legs that stood.</p>`;
      placeLabel = 'Place parlay';
    } else {
      const items = state.slip.map((x, i) =>
        `<div class="slip-item"><div><div class="slip-desc">${esc(x.desc)}</div><div class="muted small">${esc(x.selLabel)} @ ${esc(x.american)} (${Number(x.decimal).toFixed(2)})</div></div>` +
        `<input type="number" min="1" step="1" value="${x.stake}" data-i="${i}" class="stake" aria-label="Stake">` +
        `<button class="btn ghost icon rm" data-i="${i}" aria-label="Remove">✕</button>` +
        `<div class="muted small towin">To win ${fmt.credits(x.stake * (x.decimal - 1))}</div></div>`).join('');
      const total = state.slip.reduce((a, x) => a + (Number(x.stake) || 0), 0);
      body = `${items}<div class="slip-total">Total stake ${fmt.credits(total)}</div><div class="small slip-after">${afterStake(total)}</div>`;
      placeLabel = `Place ${state.slip.length} bet${state.slip.length > 1 ? 's' : ''}`;
    }
    return `<h2>Bet slip</h2>
      ${account}
      ${modeToggle}
      ${body}
      <button class="btn primary" id="place-bets" ${!me ? 'disabled title="Sign in first"' : mode === 'parlay' && parlayBlocked() ? 'disabled' : ''}>${placeLabel}</button>
      <button class="btn ghost" id="clear-slip" style="width:100%;margin-top:6px">Clear slip</button>`;
  }

  // ---- bet tickets (slip-style rendering, grouped by bettor) --------------------
  // stamp: a ticket placed a moment ago lands with a "Placed" stamp (only in the sidebar's "Your open bets").
  function betTicket(b, stamp = false) {
    const ctx = b.market_type === 'parlay' ? JSON.parse(b.context || '{}') : null;
    const legs = ctx ? ctx.legs || [] : null;
    // Linked legs were priced together (see parlayQuoteHtml): say what the odds were cut from.
    const cut = ctx && ctx.corr && ctx.corr.independent_decimal > b.odds_decimal
      ? `<div class="muted small bet-note">Linked legs: odds cut from ${fmt.oddsDec(ctx.corr.independent_decimal)}</div>` : '';
    const legIcon = { won: '✓', lost: '✗', void: '↺' };
    const legRows = legs ? legs.map((l) =>
      `<div class="bet-leg ${l.result || ''}"><span class="leg-icon">${legIcon[l.result] || '•'}</span>` +
      `<span class="leg-desc">${esc(l.description)}</span><span class="muted small">@ ${fmt.oddsDec(l.odds_decimal)}</span></div>`).join('') : '';
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
      return `<div class="bet-slip-card ${isMine({ bettor: name }) ? 'me' : ''}">
        <div class="bet-slip-head"><span class="swatch s${bettorSlot(name)} lg"></span><b>${esc(name)}</b><span class="muted small right">${betTotals(rows, settled)}</span></div>
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
        ${pending.length ? bettorSlips(pending) : '<p class="muted">No open bets. Bets settle automatically when the next 5-stack game is synced.</p>'}
        <p class="muted small">Settled bets, balances and rankings live on the <a href="#bettors">Bettors</a> tab.</p></section>`;
  }

  // The Odds & Bets sidebar's "Your open bets": the signed-in bettor's slip card from the open bets section, where a
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

  // Bettors tab: settled bets for the game picked in the dropdown (state.settledGame; '' = the most recent), grouped
  // by bettor. The dropdown lists every game with settled bets among the bets loaded.
  function settledSection() {
    const groups = settledGames();
    if (!groups.size) {
      return '<section class="card"><h2>Settled bets</h2><p class="muted">Nothing settled yet. Bets settle when the next 5-stack game is recorded.</p></section>';
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
        american: sel.american, decimal: sel.decimal, line: mk.line, stake: state.stake,
      });
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

  async function bettorSession(path) {
    const name = ($('#bettor-name')?.value || '').trim();
    const password = $('#bettor-pass')?.value || '';
    if (!name) { toast('Enter your name', 'bad'); return; }
    if (!password) { toast('Enter your betting password', 'bad'); return; }
    try {
      const r = await api(path, { method: 'POST', body: JSON.stringify({ name, password }) });
      state.bettor = r.bettor.name;
      localStorage.setItem('fs.bettor', state.bettor);
      toast(path.endsWith('register') ? `Account created. Welcome, ${r.bettor.name}.` : `Signed in as ${r.bettor.name}`, 'good');
      await loadBets();
      draw();
    } catch (e) {
      toast(e.message, 'bad');
    }
  }

  function bindSlip() {
    const slip = $('#slip');
    if (!slip) return;
    $('#bettor-signin')?.addEventListener('click', () => bettorSession('/api/bettor/login'));
    $('#bettor-register')?.addEventListener('click', () => bettorSession('/api/bettor/register'));
    $('#bettor-pass')?.addEventListener('keydown', (e) => { if (e.key === 'Enter') bettorSession('/api/bettor/login'); });
    $('#bettor-signout')?.addEventListener('click', async () => {
      try { await api('/api/bettor/logout', { method: 'POST', body: '{}' }); } catch (e) { /* cookie is cleared anyway */ }
      await loadBets();
      draw();
    });
    $('#bettor-password')?.addEventListener('click', async () => {
      const old = window.prompt('Current betting password');
      if (old == null) return;
      const nw = window.prompt('New betting password (4 to 64 characters)');
      if (nw == null) return;
      try { await api('/api/bettor/password', { method: 'POST', body: JSON.stringify({ old, new: nw }) }); toast('Password changed', 'good'); }
      catch (e) { toast(e.message, 'bad'); }
    });
    $$('.stake', slip).forEach((inp) => inp.addEventListener('input', (e) => {
      const it = state.slip[Number(e.target.dataset.i)];
      if (!it) return;
      it.stake = Math.max(0, Number(e.target.value) || 0);
      state.stake = it.stake || state.stake;
      localStorage.setItem('fs.stake', String(state.stake));
      const tw = e.target.parentElement.querySelector('.towin');
      if (tw) tw.textContent = `To win ${fmt.credits(it.stake * (it.decimal - 1))}`;
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
    bindRemove(slip);
    $('#clear-slip')?.addEventListener('click', () => { state.slip = []; drawSlip(); syncOddButtons(); });
    $('#place-bets')?.addEventListener('click', placeSlip);
    if ($('#parlay-quote', slip)) fetchParlayQuote();
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
          body: JSON.stringify({ legs: state.slip.map((x) => ({ market_id: x.market_id, selection: x.selection })), stake, context: state.ctx }),
        });
        state.slip = [];
        stampPlaced([res.bet.id]);
        toast('Parlay placed. It settles after the next 5-stack game.', 'good');
      } catch (e) {
        toast(e.message, 'bad');
      }
      await loadBets();
      draw();
      return;
    }
    const failures = [];
    const remaining = [];
    const placed = [];
    for (const it of state.slip) {
      try {
        const res = await api('/api/bets', { method: 'POST', body: JSON.stringify({ market_id: it.market_id, selection: it.selection, stake: it.stake, context: state.ctx }) });
        placed.push(res.bet.id);
      } catch (e) {
        failures.push(`${it.desc}: ${e.message}`);
        remaining.push(it);
      }
    }
    state.slip = remaining;
    stampPlaced(placed);
    if (failures.length) toast(failures.join(' · '), 'bad');
    else toast('Bets placed. They settle after the next 5-stack game.', 'good');
    await loadBets();
    draw();
  }

  // ---- bettors ----------------------------------------------------------------------
  function viewBettors() {
    const bettors = state.bettors || [];
    const start = state.status.starting_balance || 1000;
    if (!bettors.length) {
      return `<div class="card empty"><h2>No bettors yet</h2><p>Go to <a href="#odds">Odds &amp; Bets</a>, type your name in the bet slip and place a pick. Everyone starts with ${fmt.credits(start)} credits.</p></div>`;
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
    const issued = bettors.length * start + rewarded;
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
        `<td><b>${esc(b.name)}</b>${b.claimed === false ? ' <span class="muted small">unclaimed</span>' : ''}${bw ? `<div class="muted small">Best win +${fmt.credits(bw.net)} · ${esc(bw.desc)}</div>` : ''}</td>` +
        `<td class="num balance">${fmt.credits(b.balance)}</td>` +
        `<td class="bar-cell"><div class="hbar-track"><div class="hbar-fill" style="width:${Math.round((b.balance / maxBal) * 100)}%"></div></div></td>` +
        `<td class="num ${b.profit > 0 ? 'up' : b.profit < 0 ? 'down' : ''}">${fmt.signed(b.profit, 0)}</td>` +
        `<td class="num">${b.rewards ? '+' + fmt.credits(b.rewards) : '–'}</td>` +
        `<td class="num">${b.won}-${b.lost}${b.void ? '-' + b.void : ''}</td>` +
        `<td class="num">${settled ? fmt.pct(b.won / settled) : '–'}</td>` +
        `<td class="num">${b.roi != null ? fmt.signed(b.roi * 100, 0) + '%' : '–'}</td>` +
        `<td class="num">${b.pending}${b.pending_stake ? ` <span class="muted small">(${fmt.credits(b.pending_stake)})</span>` : ''}</td></tr>`;
    }).join('');
    const vizHelpers = { esc, fmt, slot: (puuid) => memberIndex().get(puuid)?.slot, bettorSlot };
    return `<section class="kpis">${kpis.join('')}</section>
      <section class="card"><h2>Rankings</h2>${how('Ordered by balance.', `Profit is betting only: it counts open stakes, is measured against the ${fmt.credits(start)} everyone started with, and leaves out game rewards (shown separately).`)}
        <div class="table-wrap"><table class="rankings"><thead><tr><th class="rank">#</th><th>Bettor</th><th class="num">Credits</th><th></th><th class="num">Profit</th><th class="num">Rewards</th><th class="num">W-L-void</th><th class="num">Win %</th><th class="num">ROI</th><th class="num">Open</th></tr></thead><tbody>${rows}</tbody></table></div>
        ${resetPanel()}</section>
      ${state.bettingReport ? window.FiveViz.bettingReport(state.bettingReport, vizHelpers) : ''}
      ${settledSection()}
      ${rewardsCard()}
      ${pastSeasonsCard()}
      ${state.bettingReport ? window.FiveViz.oddsAccuracy(state.bettingReport, vizHelpers) : ''}`;
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
      <p>This season's final standings, ${fmt.n0(cur.bets || 0)} bet${cur.bets === 1 ? '' : 's'} and ${fmt.n0(cur.rewards || 0)} game reward${cur.rewards === 1 ? '' : 's'} are saved as <b>${esc(next)}</b> under Past seasons.
        Then every bettor goes back to ${start} credits, open bets are closed, and a new season starts. Accounts and passwords stay.</p>
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
        '<th class="num">Betting profit</th><th class="num">Rewards</th><th class="num">W-L</th></tr></thead><tbody>' +
        st.map((b, i) => `<tr><td>${i + 1}</td><td><span class="swatch s${bettorSlot(b.name)}"></span>${esc(b.name)}</td>` +
          `<td class="num">${fmt.credits(b.balance)}</td><td class="num ${b.profit > 0 ? 'up' : b.profit < 0 ? 'down' : ''}">${fmt.signed(b.profit, 0)}</td>` +
          `<td class="num">${b.rewards ? '+' + fmt.credits(b.rewards) : '–'}</td><td class="num">${b.won}-${b.lost}</td></tr>`).join('') +
        '</tbody></table></div>';
      const dates = `${s.started_ts ? fmt.date(s.started_ts * 1000) : '?'} – ${fmt.date(s.ended_ts * 1000)}`;
      return `<details class="season"><summary><b>${esc(s.name)}</b><span class="muted small">${esc(dates)}</span>` +
        `${champ ? `<span>🏆 ${esc(champ.name)} · ${fmt.credits(champ.balance)}</span>` : ''}<span class="muted small right">${fmt.n0(s.bets)} bets</span></summary>${table}</details>`;
    }).join('');
    return `<section class="card"><h2>Past seasons</h2><p class="muted small">Every reset saves the season here. Click one for its final standings.</p>${rows}</section>`;
  }

  function rewardsCard() {
    const s = state.status, game = s.game_reward || 0, win = s.win_reward || 0, bonus = s.performance_bonus_max || 0;
    if (!game && !win && !bonus) return '';
    const rule = `Every squad member earns ${fmt.credits(game)} credits for each 5-stack game${win ? ` (${fmt.credits(game + win)} for a win)` : ''}, ` +
      `plus a performance bonus of up to ${fmt.credits(bonus)} based on how their ACS compares with their own previous 5-stack games: ` +
      'beat 80% of them and the bonus is 80%, rounded to the nearest 5. With fewer than 5 of those games to compare against, the bonus is half.';
    const byGame = new Map();
    (state.rewards || []).forEach((r) => {
      if (!byGame.has(r.match_id)) byGame.set(r.match_id, []);
      byGame.get(r.match_id).push(r);
    });
    const games = [...byGame.values()].slice(0, 8).map((rs) => {
      const g = rs[0], won = g.result === 'win';
      const people = rs.map((r) => {
        const why = r.beat_share == null ? 'fewer than 5 earlier 5-stack games' : `beat ${fmt.pct(r.beat_share)} of their earlier 5-stack games`;
        return `<span class="reward" title="${esc(`${r.nickname || r.bettor}: ACS ${fmt.n0(r.acs)}, ${why}`)}"><b>${esc(r.bettor)}</b> +${fmt.credits(r.base + r.bonus)}</span>`;
      }).join('');
      return `<li class="recent-row reward-row"><span class="chip ${won ? 'win' : 'loss'}">${won ? 'W' : 'L'}</span>` +
        `<span class="recent-score">${g.rounds_won ?? '?'}–${g.rounds_lost ?? '?'}</span><span>${esc(g.map || '')}</span>` +
        `<span class="rewards-list">${people}</span><span class="muted small">${fmt.date(g.started_ts ? g.started_ts * 1000 : null)}</span></li>`;
    }).join('');
    return `<section class="card"><h2>Game rewards</h2>${how(`${fmt.credits(game)} credits a game for everyone, plus a performance bonus of up to ${fmt.credits(bonus)}.`, `${rule} Hover a name for the details.`)}` +
      (games ? `<ul class="recent">${games}</ul>` : '<p class="muted">No rewards yet. They are paid when the next 5-stack game is recorded.</p>') + '</section>';
  }

  // ---- events -------------------------------------------------------------------
  // Everything betting-related that needs wiring after a page is drawn (called from app.js's bind()).
  function bind(view) {
    $$('button.odd', view).forEach((b) => b.addEventListener('click', () => toggleSlip(b.dataset.m, b.dataset.s)));
    $('#settled-game', view)?.addEventListener('change', (e) => { state.settledGame = e.target.value; draw(); });
    bindSlip();
    bindCustom(view);
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
