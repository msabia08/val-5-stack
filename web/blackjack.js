/* Blackjack against Onkey: your own solo table, or the one shared table where up to five play the same dealer hand.
   The server deals and settles everything; this page draws the felt, sends your moves and long-polls the shared
   table. Onkey (FiveCasino) comments on what happens. */
window.FiveBlackjack = (() => {
  'use strict';
  let state, $, api, draw, esc, fmt, loadMe, confetti, plainName;
  const C = window.FiveCasino;
  let which = localStorage.getItem('fs.bjTable') === 'shared' ? 'shared' : 'solo';
  const data = { solo: null, shared: null };
  const seen = { solo: null, shared: null };   // the last log entry Onkey reacted to, per table
  let stake = +(localStorage.getItem('fs.bjStake') || 10), busy = false, error = '', loop = null, owner;
  const RESULT = { win: 'Win', lose: 'Lose', push: 'Push', blackjack: 'Blackjack', bust: 'Bust' };
  function init(ctx) { ({ state, $, api, draw, esc, fmt, loadMe, confetti, plainName } = ctx); }

  const url = (since) => `/api/blackjack?table=${which}${since != null ? `&since=${since}` : ''}`;
  async function load() {
    const name = state.me?.name || null;
    if (owner !== name) { owner = name; data.solo = data.shared = null; seen.solo = seen.shared = null; C.dealReset(); }
    apply(await api(url()));
  }
  // A fresh table state from the server: patch the regions that changed, and let Onkey react to what happened.
  function apply(d) {
    const t = d.table === 'shared' ? 'shared' : 'solo';
    const old = data[t];
    if (old && d.version < old.version && t === 'shared') return; // an older answer arriving late
    data[t] = d;
    C.syncClock(d.server_time);
    C.absorb(d.looks);
    const fresh = (d.log || []).filter((e) => seen[t] != null && e.id > seen[t]);
    seen[t] = d.log?.length ? d.log[d.log.length - 1].id : seen[t] ?? 0;
    if (state.view !== 'blackjack' || t !== which) return;
    if (!$('#bj-root')) return;
    refresh();
    const wait = C.landing();
    if (wait > 0) setTimeout(() => { if (state.view === 'blackjack' && $('#bj-root')) refresh(); }, wait + 30); // controls come back once the cards are down
    if (fresh.length) setTimeout(() => react(fresh), wait);
  }
  // What happened, once the cards are down: Onkey's line, Table win bursts, your win.
  function react(fresh) {
    if (state.view !== 'blackjack' || !$('#bj-root')) return;
    C.react($('#bj-onkey'), fresh, (e) => e.kind, (e) => ({ name: e.bettor || 'Onkey', amount: fmt.credits(Math.abs(e.amount || 0)) }));
    fresh.filter((e) => e.kind === 'win' && e.bettor).forEach((e) => C.seatBurst($('#bj-spots'), e.bettor)); // bought Table wins
    const mine = fresh.find((e) => e.bettor === state.me?.name && ['win', 'push', 'lose'].includes(e.kind));
    if (mine?.kind === 'win') {
      C.sound('win');
      if (mine.blackjack || mine.amount >= 100) confetti($('#me-credits'), mine.amount >= 250);
    }
    if (mine) loadMe();
  }
  function startLoop() {
    if (which !== 'shared' || (loop && loop.running)) return;
    loop = C.live({
      alive: () => state.view === 'blackjack' && which === 'shared',
      version: () => data.shared?.version ?? -1,
      fetchNext: (v) => api(url(v)),
      apply,
    });
  }

  // ---- drawing -------------------------------------------------------------------------
  function view() {
    const d = data[which];
    if (!d || (d.me?.name || null) !== (state.me?.name || null)) {
      load().then(() => { if (state.view === 'blackjack') draw(); }).catch((e) => {
        const el = $('#bj-loading'); if (el) el.textContent = `Could not load the table: ${e.message}`;
      });
      return '<section class="card" id="bj-loading">Shuffling the shoe…</section>';
    }
    const seats = d.shared_seats;
    const felt = C.deal(() => ({ dealer: dealerHtml(d), spots: spotsHtml(d), status: statusHtml(d) }));
    return `<div id="bj-root" class="casino-page">
      <div class="casino-bar">
        <div class="seg" role="tablist" aria-label="Table">
          <button class="seg-btn ${which === 'solo' ? 'on' : ''}" role="tab" aria-selected="${which === 'solo'}" data-bj-table="solo">Solo table</button>
          <button class="seg-btn ${which === 'shared' ? 'on' : ''}" role="tab" aria-selected="${which === 'shared'}" data-bj-table="shared">Shared table <span class="seg-count">${seats.taken}/${seats.max}</span></button>
        </div>
        <span class="muted small">${which === 'solo' ? 'Just you and Onkey. The deal waits for you.' : 'Everyone at this table plays the same dealer hand.'}</span>
        ${C.speaker()}
      </div>
      <div class="casino-layout">
        <section class="felt bj-felt" aria-label="Blackjack table">
          <div class="felt-top">${C.dealer('bj-onkey')}<div class="bj-dealer" id="bj-dealer">${felt.dealer}</div></div>
          <p class="felt-print" aria-hidden="true">Blackjack pays 3 to 2 · Dealer stands on all 17s</p>
          <div class="bj-spots ${which}" id="bj-spots">${felt.spots}</div>
          <div class="felt-status" id="bj-status" role="status" aria-live="polite">${felt.status}</div>
          <div class="felt-controls" id="bj-controls"><div class="casino-controls">${controlsHtml(d)}</div></div>
        </section>
        <aside class="casino-side" id="bj-side">${sideHtml(d)}</aside>
      </div>
    </div>`;
  }
  function refresh() {
    const d = data[which];
    const felt = C.deal(() => ({ dealer: dealerHtml(d), spots: spotsHtml(d), status: statusHtml(d) }));
    C.patch($('#bj-dealer'), felt.dealer);
    C.patch($('#bj-spots'), felt.spots);
    C.patch($('#bj-status'), felt.status);
    C.patch($('#bj-controls'), `<div${C.after('casino-controls')}>${controlsHtml(d)}</div>`);
    C.patch($('#bj-side'), sideHtml(d));
    const count = $('#bj-root .seg-count');
    if (count) count.textContent = `${d.shared_seats.taken}/${d.shared_seats.max}`;
  }
  function totalBadge(total, soft, extra = '') {
    return `<span class="hand-total ${extra}">${soft ? `${total - 10}/${total}` : total}</span>`;
  }
  function dealerHtml(d) {
    const cs = d.dealer.cards;
    if (!cs.length) return '<div class="bj-cards">' + C.card('') + C.card('') + '</div><span class="muted small">Onkey is waiting for bets</span>';
    const hidden = cs.includes(null);
    const back = which === 'solo' && d.me ? C.style(d.me.name, 'card_back') : '';
    const key = `bj:${d.table}:${d.round}:dealer`;
    const seq = (i) => (i === 0 ? 99 : i === 1 ? (cs[1] ? 9000 : 199) : 10000 + i);
    return `<div class="bj-cards">${cs.map((c, i) => C.card(c, { size: 'lg', cls: c ? '' : back, key: `${key}:${i}`, seq: seq(i) })).join('')}</div>` +
      `<div${C.after('bj-total')}>${d.dealer.blackjack ? '<span class="hand-total bj">Blackjack</span>' : totalBadge(d.dealer.total, false, d.dealer.total > 21 ? 'bust' : '')}${hidden ? '<span class="muted small">showing</span>' : ''}</div>`;
  }
  function handHtml(h, chips = '', key = '', seat = 0, hi = 0) {
    const res = h.result ? `<span class="hand-result ${h.result}">${RESULT[h.result]}${h.payout ? ` +${fmt.credits(h.payout)}` : ''}</span>` : '';
    const badge = h.blackjack ? '<span class="hand-total bj">21</span>' : totalBadge(h.total, h.soft, h.total > 21 ? 'bust' : '');
    return `<div class="bj-hand ${h.turn ? 'active' : ''} ${h.result || ''}">
      <div class="bj-cards">${C.cards(h.cards, { size: 'lg', key: key && `${key}:${hi}`, seq: (i) => (hi === 0 && i < 2 ? i * 100 + seat : 5000 + hi * 10 + i) })}</div>
      <div${C.after('bj-hand-meta')}>${badge}<span class="chip-stake ${chips}" title="Stake">${fmt.credits(h.stake)}${h.doubled ? ' ×2' : ''}</span>${res}</div></div>`;
  }
  function spotsHtml(d) {
    const meName = d.me?.name;
    const spots = d.seats.map((s) => {
      const turn = d.turn && d.turn.bettor === s.bettor;
      const chips = C.style(s.bettor, 'chips');
      const seat = d.seats.indexOf(s), key = `bj:${d.table}:${d.round}:${s.bettor}`;
      const hands = s.hands.length ? s.hands.map((h, hi) => handHtml(h, chips, key, seat, hi)).join('')
        : s.stake ? `<div class="bj-hand waiting"><span class="chip-stake big ${chips}">${fmt.credits(s.stake)}</span><span class="muted small">Bet placed</span></div>`
          : `<div class="bj-hand waiting"><span class="muted small">${d.phase === 'betting' ? 'No bet yet' : 'Sitting this one out'}</span></div>`;
      return `<div class="bj-spot ${turn ? 'turn' : ''} ${s.bettor === meName ? 'me' : ''} ${C.style(s.bettor, 'seat')}" data-bettor="${esc(s.bettor)}">
        <div class="bj-hands">${hands}</div>
        <div class="bj-spot-name">${C.who(s.bettor, { title: true })}${turn && which === 'shared' ? `<span class="muted small">${s.bettor === meName ? 'Your turn' : 'Their turn'}</span>` : ''}</div>
        ${turn ? C.timer(d.deadline, d.turn_s) : ''}</div>`;
    });
    if (which === 'shared') for (let i = d.seats.length; i < d.max_seats; i++) spots.push('<div class="bj-spot open"><span class="muted small">Open seat</span></div>');
    if (which === 'solo' && !spots.length) spots.push(`<div class="bj-spot open"><span class="muted small">${state.me ? 'Pick a stake and deal' : 'Sign in to play'}</span></div>`);
    return spots.join('');
  }
  function statusHtml(d) {
    const line = statusLine(d);
    return line && !error ? `<span${C.after()}>${line}</span>` : line;
  }
  function statusLine(d) {
    if (error) return `<span class="casino-error">${esc(error)}</span>`;
    if (d.phase === 'playing' && d.turn) {
      const mine = d.turn.bettor === d.me?.name;
      const hands = (d.seats.find((s) => s.bettor === d.turn.bettor)?.hands || []).length;
      const label = hands > 1 ? ` (hand ${d.turn.hand + 1})` : '';
      return mine ? `<b>Your move${label}.</b>${which === 'shared' ? ` ${C.countdown(d.deadline)} left, then Onkey stands for you.` : ''}`
        : `${plainName(d.turn.bettor)} is playing${label}. ${C.countdown(d.deadline)}`;
    }
    if (d.phase === 'done') {
      const mine = d.seats.find((s) => s.bettor === d.me?.name && s.hands.length);
      const line = mine ? (() => {
        const net = mine.hands.reduce((a, h) => a + h.payout - h.stake, 0);
        return net > 0 ? `<b class="up">You won ${fmt.credits(net)}.</b>` : net < 0 ? `<b class="down">You lost ${fmt.credits(-net)}.</b>` : '<b>Push. Your stake is back.</b>';
      })() : 'Round over.';
      return which === 'shared' ? `${line} Next round in ${C.countdown(d.next_round_at)}` : `${line} Deal again when you're ready.`;
    }
    if (which === 'shared') {
      if (!d.seats.length) return 'Nobody is at the shared table yet.';
      const bets = d.seats.filter((s) => s.stake).length;
      return bets ? `Betting closes in ${C.countdown(d.deadline)}, or as soon as everyone seated has bet (${bets} of ${d.seats.length}).`
        : 'Place your bets. Betting closes 15 seconds after the first one.';
    }
    return state.me ? 'Pick a stake and deal.' : 'Sign in to play blackjack with your credits.';
  }
  function stakeKeys(d, disabled) {
    return `<div class="casino-stakes" role="group" aria-label="Stake">${d.stakes.map((s) => `<button type="button" class="stake-key ${d.me ? C.style(d.me.name, 'chips') : ''}" data-bj-stake="${s}" aria-pressed="${s === stake}" ${disabled || (d.me && d.me.balance < s) ? 'disabled' : ''}>${s}</button>`).join('')}</div>`;
  }
  function controlsHtml(d) {
    const me = d.me;
    if (!me) return '<button class="btn primary casino-cta" data-signin>Sign in to play</button>';
    const acts = me.actions || [];
    if (acts.length) {
      const hand = d.seats.find((s) => s.bettor === me.name)?.hands[d.turn.hand];
      const btn = (a, label, sub = '') => `<button class="btn casino-act act-${a}" data-bj-act="${a}" ${busy || !acts.includes(a) ? 'disabled' : ''}>${label}${sub ? `<small>${sub}</small>` : ''}</button>`;
      return `<div class="casino-actions">${btn('hit', 'Hit')}${btn('stand', 'Stand')}${btn('double', 'Double', hand ? `+${fmt.credits(hand.stake)}` : '')}${btn('split', 'Split', hand ? `+${fmt.credits(hand.stake)}` : '')}</div>`;
    }
    if (which === 'shared') {
      if (!me.seated) {
        const full = d.shared_seats.taken >= d.shared_seats.max;
        return `<button class="btn primary casino-cta" data-bj-sit ${full || busy ? 'disabled' : ''}>${full ? 'The table is full' : 'Sit down'}</button>`;
      }
      const canBet = d.phase === 'betting' && !me.bet;
      return `<div class="casino-deck">${stakeKeys(d, !canBet || busy)}
        <button class="btn primary casino-cta" data-bj-bet ${!canBet || busy || me.balance < stake ? 'disabled' : ''}>${me.bet ? 'Bet placed' : `Bet ${stake}`}</button>
        <button class="btn ghost" data-bj-leave ${busy || (d.phase === 'playing' && d.seats.some((s) => s.bettor === me.name && s.hands.some((h) => !h.done))) ? 'disabled' : ''}>Leave table</button></div>`;
    }
    const playing = d.phase === 'playing';
    return `<div class="casino-deck">${stakeKeys(d, playing || busy)}
      <button class="btn primary casino-cta" data-bj-bet ${playing || busy || me.balance < stake ? 'disabled' : ''}>Deal · ${stake}</button></div>`;
  }
  function sideHtml(d) {
    const s = d.me?.season;
    const tiles = s ? `<div class="casino-tiles">
        <div class="tile"><div class="tile-label">Hands</div><div class="tile-value">${fmt.n0(s.hands)}</div></div>
        <div class="tile"><div class="tile-label">Net</div><div class="tile-value ${s.net > 0 ? 'up' : s.net < 0 ? 'down' : ''}">${fmt.signed(s.net, 0)}</div></div>
        <div class="tile"><div class="tile-label">Won</div><div class="tile-value">${fmt.n0(s.wins)}</div></div>
        <div class="tile"><div class="tile-label">Blackjacks</div><div class="tile-value">${fmt.n0(s.blackjacks)}</div></div></div>`
      : '<p class="muted"><a href="#" data-signin>Sign in</a> to play and see your season.</p>';
    const at = d.shared_seats.names;
    return `<section class="card"><h2>Your season</h2>${tiles}</section>
      <section class="card"><h2>The shared table</h2>${at.length ? `<ul class="casino-names">${at.map((n) => `<li>${C.who(n)}</li>`).join('')}</ul>` : '<p class="muted">Empty. Sit down and Onkey deals for whoever joins.</p>'}
        <p class="muted small">${d.shared_seats.taken} of ${d.shared_seats.max} seats taken.</p></section>
      <section class="card"><h2>House rules</h2><ul class="casino-rules">${d.rules.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>
        <details class="how"><summary>The house keeps about ${(d.edge * 100).toFixed(1)}% over time.</summary><p>That edge is the house's cut. Results count in the Casino column in Standings, separate from match-betting profit, and the casino never earns or costs bananas. A hand still open when the server restarts or the season ends is refunded.</p></details></section>`;
  }

  // ---- moves -----------------------------------------------------------------------------
  async function post(path, body) {
    if (busy) return;
    busy = true; error = '';
    refresh();
    try {
      apply(await api(path, { method: 'POST', body: JSON.stringify({ table: which, ...body }) }));
      setTimeout(loadMe, C.landing()); // the credits chip changes when the cards are down, not before
    } catch (e) {
      error = e.message;
      try { apply(await api(url())); } catch (_) { /* keep the old state */ }
    } finally {
      busy = false;
      if ($('#bj-root')) refresh();
    }
  }
  function bind(viewEl) {
    const root = viewEl.querySelector('#bj-root');
    if (!root) return;
    C.bindSpeaker(root);
    root.addEventListener('click', (e) => {
      const t = e.target.closest('button');
      if (!t || t.disabled) return;
      if (t.dataset.bjTable && t.dataset.bjTable !== which) {
        which = t.dataset.bjTable; error = ''; C.dealReset();
        try { localStorage.setItem('fs.bjTable', which); } catch (_) { /* unavailable */ }
        loop?.stop(); loop = null;
        draw();
      } else if (t.dataset.bjStake) {
        stake = +t.dataset.bjStake;
        try { localStorage.setItem('fs.bjStake', String(stake)); } catch (_) { /* unavailable */ }
        refresh();
      } else if (t.hasAttribute('data-bj-bet')) {
        C.sound('chips');
        post('/api/blackjack/bet', { stake, request_id: C.ref() });
      } else if (t.dataset.bjAct) {
        post('/api/blackjack/action', { action: t.dataset.bjAct, step: data[which].me.step });
      } else if (t.hasAttribute('data-bj-sit')) {
        post('/api/blackjack/sit', {});
      } else if (t.hasAttribute('data-bj-leave')) {
        post('/api/blackjack/leave', {});
      }
    });
    // Keyboard: H hit, S stand, D double, P split, Enter deals.
    if (!bind.keys) {
      bind.keys = true;
      document.addEventListener('keydown', (e) => {
        if (state.view !== 'blackjack' || e.target.closest('input, textarea, select, button, a') || e.metaKey || e.ctrlKey || e.altKey) return;
        const key = { h: 'hit', s: 'stand', d: 'double', p: 'split' }[e.key.toLowerCase()];
        const b = key ? $(`[data-bj-act="${key}"]`) : e.key === 'Enter' ? $('[data-bj-bet]') : null;
        if (b && !b.disabled) { e.preventDefault(); b.click(); }
      });
    }
    if (!bind.greeted) { bind.greeted = true; setTimeout(() => C.say($('#bj-onkey'), 'greet'), 500); }
    startLoop();
  }
  return { init, load, view, bind };
})();
