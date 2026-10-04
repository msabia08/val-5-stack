/* Crash (fivestack/crash.py): Onkey's banana rocket (a banana with fins and a flame, Onkey riding it; `SHIP`-style
   inline SVG in `view()`, drawn nose to the right and turned along the curve from its tail). One round for everyone: bets go down on the pad, the rocket launches a
   few seconds after the first one, its multiplier climbs, and it crashes at a point only the server knows. Cash out
   before that for your stake times the multiplier; still aboard at the crash, the stake is gone.

   The server's clock decides everything. This page only draws the climb: the multiplier is e^(growth x seconds since
   `started`), by the server's clock (`offset`), repainted every frame while the rocket flies (`paint()`: the big
   number, the curve, the rocket at its tip, the Cash out button's amount). The sky's elements are made once in
   `view()`; `refresh()` patches the regions round it. A bet made while a round is in the air is `queued` and sent as
   soon as the pad opens. */
window.FiveCrash = (() => {
  'use strict';
  let state, $, api, draw, esc, fmt, loadMe, confetti;
  const C = window.FiveCasino;
  const stored = (key, fallback) => { try { return localStorage.getItem(key) ?? fallback; } catch (_) { return fallback; } };
  const store = (key, value) => { try { localStorage.setItem(key, value); } catch (_) { /* unavailable */ } };
  let data = null, owner, loop = null, raf = 0;
  let offset = 0; // the server's clock minus ours, seconds
  let seen = null; // the last log entry the page reacted to
  let stake = +stored('fs.crStake', 25);
  let auto = stored('fs.crAuto', ''); // the auto cash-out field, as typed
  let busy = false, error = '', queued = false;

  function init(ctx) {
    ({ state, $, api, draw, esc, fmt, loadMe, confetti } = ctx);
    // Space cashes out (or bets): the one key that matters when the rocket is climbing.
    document.addEventListener('keydown', (e) => {
      if (e.code !== 'Space' || e.repeat || state.view !== 'crash' || !$('#cr-root') || e.target.closest('input, textarea, select, button, a')) return;
      e.preventDefault();
      if (canCash()) cashOut(); else if (canBet()) bet();
    });
  }

  const url = (since) => `/api/crash${since != null ? `?since=${since}` : ''}`;
  async function load() {
    const name = state.me?.name || null;
    if (owner !== name) { owner = name; data = null; seen = null; queued = false; error = ''; }
    apply(await api(url()), true);
  }
  function startLoop() {
    loop?.stop();
    loop = C.live({ alive: () => state.view === 'crash', fetchNext: (v) => api(url(v)), apply: (d) => apply(d), version: () => data?.version ?? 0 });
  }

  // ---- the numbers --------------------------------------------------------------------------------------------------
  const serverNow = () => Date.now() / 1000 + offset;
  const x2 = (m) => `${(+m).toFixed(2)}x`;
  const multAt = (d, seconds) => (seconds <= 0 ? 1 : Math.min(d.max_mult, Math.floor(Math.exp(d.growth * seconds) * 100 + 1e-9) / 100));
  const timeTo = (d, m) => Math.log(Math.max(1, m)) / d.growth;
  // Seconds into the flight to draw, and the multiplier there.
  function flight(d) {
    if (d.phase === 'crashed') return { t: timeTo(d, d.crash), m: d.crash };
    if (d.phase !== 'flying') return { t: 0, m: 1 };
    const t = Math.max(0, serverNow() - d.started);
    return { t, m: multAt(d, t) };
  }
  const mine = (d) => (d && d.me ? d.me.bet : null);
  const canBet = () => !!data && !!data.me && !busy && !mine(data) && !queued;
  const canCash = () => !!data && data.phase === 'flying' && !busy && !!mine(data) && mine(data).cashout == null;
  const autoValue = (d) => {
    const v = parseFloat(String(auto).replace(',', '.'));
    return auto === '' || Number.isNaN(v) ? null : Math.round(v * 100) / 100;
  };
  const autoBad = (d) => { const v = autoValue(d); return auto !== '' && (v == null || v < d.min_auto || v > d.max_mult); };
  const tone = (m) => (m >= 10 ? 'gold' : m >= 2 ? 'up' : 'down');

  // A fresh state from the server.
  function apply(d, first) {
    if (data && d.version < data.version) return; // an older answer arriving late
    const old = data;
    data = d;
    offset = d.server_time - Date.now() / 1000;
    C.syncClock(d.server_time);
    C.absorb(d.looks);
    const fresh = (d.log || []).filter((e) => seen != null && e.id > seen);
    seen = d.log && d.log.length ? d.log[d.log.length - 1].id : (seen ?? 0);
    const here = state.view === 'crash' && $('#cr-root');
    if (here) {
      refresh();
      if (!first) fresh.forEach((e) => react(d, e));
      if (old && old.me && d.me && old.me.balance !== d.me.balance) loadMe?.();
    }
    // The pad is open again: send the bet that was waiting for it.
    if (queued && d.phase === 'betting' && d.me && !mine(d) && !busy) { queued = false; bet(); }
    if (state.view === 'crash' && !loop?.running) startLoop();
  }
  function react(d, e) {
    const me = d.me?.name;
    if (e.kind === 'launch') C.sound('launch');
    else if (e.kind === 'crash') {
      C.sound(e.moon ? 'win' : 'boom');
      const b = mine(d);
      if (b && b.cashout == null) window.FiveOnkey?.note('crash', { kind: e.mult <= 1 ? 'pad' : 'lost', mult: x2(e.mult) });
      else if (e.moon && b) window.FiveOnkey?.note('crash', { kind: 'moon' });
    } else if (e.kind === 'cashout' && e.bettor === me) {
      C.sound('win');
      const sky = $('#cr-sky');
      if (sky && confetti && e.mult >= 2) confetti(sky, e.mult >= 10);
      window.FiveOnkey?.note('crash', { kind: 'out', mult: x2(e.mult), big: e.mult >= 10, amount: fmt.credits(e.amount) });
    } else if (e.kind === 'cashout') C.sound('chips');
  }

  // ---- the sky: repainted every frame while the rocket flies ---------------------------------------------------------
  // The curve's box (SVG units): the pad sits at (X0, Y0), in from the corner so the rocket shows whole, and the
  // curve may use TOP of the height above it.
  const W = 1000, H = 400, X0 = 48, Y0 = H - 44, TOP = 0.92;
  const BASE_T = 6, BASE_M = 1.8; // the view on the pad: seconds across, and the multiplier at the top
  // The plants on the ground: one stretch of jungle (x in % of the pad's view, size, emoji) repeated to the right, far
  // enough that the ground is still planted when the view has pulled all the way back.
  const STRETCH = [[17, 96, '🌴'], [36, 44, '🌿'], [58, 120, '🌴'], [74, 40, '🌿'], [89, 88, '🌴'], [104, 46, '🌿']];
  const STRETCH_W = 112, STRETCHES = 9;
  const PLANTS = Array.from({ length: STRETCHES }, (_, k) => STRETCH.map(([x, size, emoji], i) => {
    const vary = 0.8 + (((k * 7 + i * 5) % 6) / 5) * 0.4; // no two stretches quite alike
    return `<span class="plant" style="--x:${x + k * STRETCH_W + ((k * 3 + i) % 4) * 2}%;font-size:${Math.round(size * vary)}px">${emoji}</span>`;
  }).join('')).join('');
  const TREE_MIN = 0.1; // the endless tree never gets thinner than this share of its size on the pad
  const HEAD = 1.22; // the view's top stays this far above the rocket
  const CLIMB = 110; // pixels of tree that pass per second once the view is pulling back
  const STEPS = [0.1, 0.25, 0.5, 1, 2, 5, 10, 25, 50];
  function paint() {
    const d = data, sky = $('#cr-sky');
    if (!d || !sky) return;
    const { t, m } = flight(d);
    const tMax = Math.max(BASE_T, t * 1.3), mMax = Math.max(BASE_M, 1 + (m - 1) * HEAD);
    // The endless tree stands still until the view starts pulling back (the rocket nearing the top of the pad's
    // view), then slides down for as long as the rocket goes up.
    const pullsBack = Math.log(1 + (BASE_M - 1) / HEAD) / d.growth;
    sky.style.setProperty('--climb', `${Math.round(Math.max(0, t - pullsBack) * CLIMB)}px`);
    // The view pulls back as the rocket climbs: the plants on the ground shrink and close up toward the pad with it.
    const zoom = (BASE_M - 1) / (mMax - 1);
    sky.style.setProperty('--zoom', Math.max(0.03, zoom).toFixed(4));
    sky.style.setProperty('--tree', Math.max(TREE_MIN, zoom).toFixed(4)); // the endless tree shrinks with them, to a floor
    sky.style.setProperty('--zoom-x', (BASE_T / tMax).toFixed(4));
    const px = (s) => X0 + (s / tMax) * (W - X0), py = (v) => Y0 - ((v - 1) / (mMax - 1)) * Y0 * TOP;
    const pts = [];
    for (let i = 0; i <= 48; i++) { const s = (t * i) / 48; pts.push(`${px(s).toFixed(1)} ${py(Math.exp(d.growth * s)).toFixed(1)}`); }
    const line = $('#cr-line'), fill = $('#cr-fill');
    if (line) line.setAttribute('d', t > 0 ? `M${pts.join(' L')}` : '');
    if (fill) fill.setAttribute('d', t > 0 ? `M${X0} ${Y0} L${pts.join(' L')} L${px(t).toFixed(1)} ${Y0} Z` : '');
    // The rocket rides the tip, pointing along the curve as the screen draws it.
    const rocket = $('#cr-rocket');
    if (rocket) {
      const tip = Math.exp(d.growth * t);
      const slope = Math.atan2(d.growth * tip * (sky.clientHeight * (Y0 / H) * TOP) / (mMax - 1), (sky.clientWidth * (1 - X0 / W)) / tMax) * 180 / Math.PI;
      rocket.style.left = `${(px(t) / W) * 100}%`;
      rocket.style.top = `${(py(tip) / H) * 100}%`;
      rocket.style.setProperty('--tilt', `${(-(t > 0 ? slope : 90)).toFixed(1)}deg`); // the ship is drawn nose to the right
    }
    // Gridlines at round multipliers.
    const grid = $('#cr-grid');
    if (grid) {
      const step = STEPS.find((s) => (mMax - 1) / s <= 5) || 50, lines = [];
      for (let v = 1 + step; v < mMax; v += step) lines.push(`<span style="top:${((py(v) / H) * 100).toFixed(2)}%">${+v.toFixed(2)}x</span>`);
      C.patch(grid, lines.join(''));
    }
    const big = $('#cr-mult');
    if (big && d.phase !== 'betting') big.textContent = x2(m);
    const amt = $('#cr-cash-amt');
    const b = mine(d);
    if (amt && b) amt.textContent = fmt.credits(b.stake * m);
    document.querySelectorAll('#cr-side [data-live-stake]').forEach((el) => { el.textContent = `${fmt.credits(+el.dataset.liveStake * m)}`; });
  }
  function frame() {
    raf = 0;
    if (state.view !== 'crash' || !data || !$('#cr-root')) return;
    paint();
    if (data.phase === 'flying') raf = requestAnimationFrame(frame);
  }
  const animate = () => { if (!raf) raf = requestAnimationFrame(frame); };

  // ---- the page ----------------------------------------------------------------------------------------------------
  function centerHtml(d) {
    if (d.phase === 'flying') return '<div class="cr-mult" id="cr-mult">1.00x</div>';
    if (d.phase === 'crashed') {
      const moon = d.crash >= d.max_mult;
      return `<div class="cr-tag">${moon ? 'It made it!' : d.crash <= 1 ? 'Never left the pad' : 'Crashed at'}</div><div class="cr-mult" id="cr-mult">${x2(d.crash)}</div>`;
    }
    if (d.deadline) return `<div class="cr-tag">Launching in</div><div class="cr-mult small">${C.countdown(d.deadline)}</div>`;
    return '<div class="cr-tag">On the pad</div><div class="cr-mult small idle">Waiting for a bet</div>';
  }
  function historyHtml(d) {
    return (d.history || []).slice(0, 16).map((m, i) => `<span class="cr-past ${tone(m)}${i === 0 ? ' latest' : ''}">${x2(m)}</span>`).join('')
      || '<span class="muted small">No rounds yet</span>';
  }
  function statusHtml(d) {
    if (error) return `<span class="casino-error">${esc(error)}</span>`;
    if (!d.me) return '<a href="#" data-signin>Sign in</a> to play.';
    const b = mine(d);
    if (d.phase === 'betting') {
      if (d.deadline) return `${b ? 'You\'re aboard. ' : ''}Launching in ${C.countdown(d.deadline)} ${C.timer(d.deadline, d.bet_window_s)}`;
      return `Pick a stake and bet. The rocket launches ${d.bet_window_s} seconds after the first bet.`;
    }
    if (d.phase === 'flying') {
      if (!b) return queued ? 'In flight. Your bet is in for the next round.' : 'In flight. Bet now to be on the next one.';
      if (b.cashout != null) return `You're out at ${x2(b.cashout)}: <b class="up">+${fmt.credits(b.paid - b.stake)} credits</b>. Watching the rest.`;
      return b.auto ? `Cashing out by itself at ${x2(b.auto)}, or sooner if you say so.` : 'Cash out before it crashes.';
    }
    const next = d.next_round_at ? ` <span class="muted">Next round in ${C.countdown(d.next_round_at)}</span>` : '';
    const where = d.crash <= 1 ? 'It never left the pad.' : d.crash >= d.max_mult ? `All the way to ${x2(d.crash)}.` : `Crashed at ${x2(d.crash)}.`;
    if (!b) return where + next;
    if (b.cashout != null) return `${where} You got out at ${x2(b.cashout)}: <b class="up">+${fmt.credits(b.paid - b.stake)} credits</b>.${next}`;
    return `${where} <span class="down">You lost ${fmt.credits(b.stake)} credits.</span>${next}`;
  }
  function controlsHtml(d) {
    if (!d.me) return '';
    const b = mine(d);
    if (b && d.phase === 'flying' && b.cashout == null) {
      return `<button class="btn cr-cash" data-cr-cash ${busy ? 'disabled' : ''}><span>Cash out</span><b id="cr-cash-amt">${fmt.credits(b.stake)}</b></button>`;
    }
    if (b && d.phase !== 'crashed') {
      const what = b.cashout != null ? `Out at <b>${x2(b.cashout)}</b> with <b class="up">${fmt.credits(b.paid)}</b>` : `Aboard with <b>${fmt.credits(b.stake)}</b>${b.auto ? ` · auto cash-out at <b>${x2(b.auto)}</b>` : ''}`;
      return `<div class="cr-aboard">${what}</div>`;
    }
    const chips = d.stakes.map((s) => `<button type="button" class="rl-pick${s === stake ? ' on' : ''}" data-cr-stake="${s}" aria-pressed="${s === stake}" aria-label="${s} credits">${C.chip(s, { cls: 'mini' })}</button>`).join('');
    const open = d.phase === 'betting';
    const short = d.me.balance < stake;
    const label = queued ? 'Bet is in for the next round' : open ? `Bet ${fmt.credits(stake)}` : `Bet ${fmt.credits(stake)} on the next round`;
    return `<div class="rl-chips" role="group" aria-label="Stake">${chips}</div>
      <label class="cr-auto${autoBad(d) ? ' bad' : ''}"><span>Auto cash-out</span>
        <span class="cr-auto-field"><input id="cr-auto" type="text" inputmode="decimal" autocomplete="off" placeholder="Off" value="${esc(auto)}" aria-label="Auto cash-out multiplier">x</span></label>
      ${queued ? `<button class="btn ghost cr-go" data-cr-unqueue>${label} <span class="muted small">(click to take it back)</span></button>`
    : `<button class="btn primary cr-go" data-cr-bet ${busy || short || autoBad(d) ? 'disabled' : ''}>${short ? 'Not enough credits' : label}</button>`}`;
  }
  function sideHtml(d) {
    const flying = d.phase === 'flying';
    const row = (s) => {
      const state_ = s.cashout != null ? `<b class="up">${x2(s.cashout)} · +${fmt.credits(s.paid - s.stake)}</b>`
        : s.lost ? `<b class="down">-${fmt.credits(s.stake)}</b>`
          : flying ? `<span class="cr-live" data-live-stake="${s.stake}">${fmt.credits(s.stake)}</span>` : '<span class="muted">aboard</span>';
      return `<li class="${s.cashout != null ? 'out' : s.lost ? 'lost' : ''}"><span>${C.who(s.bettor)} <span class="muted small">${fmt.credits(s.stake)}</span></span><span class="num">${state_}</span></li>`;
    };
    const seats = `<section class="card"><h2>Aboard this round</h2>${d.seats.length ? `<ul class="rl-bets cr-seats">${d.seats.map(row).join('')}</ul>` : '<p class="muted small">Nobody yet. The first bet starts the countdown.</p>'}</section>`;
    const s = d.me && d.me.season;
    const season = s ? `<section class="card"><h2>Your season</h2><div class="rl-season">
        <div><span class="tile-label">Rounds</span><b>${fmt.n0(s.rounds)}</b></div>
        <div><span class="tile-label">Net</span><b class="${s.net > 0 ? 'up' : s.net < 0 ? 'down' : ''}">${s.net > 0 ? '+' : ''}${fmt.credits(s.net)}</b></div>
        <div><span class="tile-label">Got out in time</span><b>${s.rounds ? `${fmt.n0(s.cashed)} of ${fmt.n0(s.rounds)}` : '–'}</b></div>
        <div><span class="tile-label">Highest cash-out</span><b>${s.best_mult ? x2(s.best_mult) : '–'}</b></div></div></section>` : '';
    const reach = [1.5, 2, 3, 5, 10, 25, 50, 100].filter((m) => m <= d.max_mult);
    const rules = `<section class="card"><h2>How far it gets</h2><table class="compact rl-pays"><tbody>${reach.map((m) => `<tr><th scope="row">Reaches ${m}x</th><td class="num">${(((1 - d.edge) / m) * 100).toFixed(1)}%</td></tr>`).join('')}</tbody></table>
      <details class="how"><summary>Where it crashes is drawn at launch.</summary><div class="how-body">${d.rules.map((r) => `${esc(r)}.`).join(' ')} It stays on the server until the rocket crashes. The chance it reaches a multiplier is ${fmt.pct(1 - d.edge)} divided by that multiplier, so every cash-out point is worth the same: the house keeps ${fmt.pct(d.edge)} of the stakes on average, and about ${Math.round(d.edge * 100)} rounds in 100 never leave the pad. A cash-out counts when it reaches the server. Space bar bets and cashes out.</div></details></section>`;
    return `${seats}${rules}${season}`;
  }

  function view() {
    const d = data;
    if (!d || (d.me?.name || null) !== (state.me?.name || null)) {
      load().then(() => { if (state.view === 'crash') draw(); }).catch((e) => {
        const el = $('#cr-loading'); if (el) el.textContent = `Could not load the rocket: ${e.message}`;
      });
      return '<section class="card" id="cr-loading">Fuelling the rocket…</section>';
    }
    return `<div id="cr-root" class="casino-page">
      <div class="casino-bar">
        <span class="muted small">One rocket, everyone aboard. The multiplier climbs until it crashes: cash out first.</span>
        ${C.speaker()}
      </div>
      <div class="casino-layout">
        <section class="cr-stage" aria-label="Crash">
          <div class="cr-history" id="cr-history" aria-label="Earlier rounds">${historyHtml(d)}</div>
          <div class="cr-sky ${d.phase}" id="cr-sky">
            <div class="cr-deco" aria-hidden="true"><span class="vine" style="left:6%;font-size:54px">🌿</span><span class="vine" style="left:27%;font-size:44px">🌿</span><span class="vine" style="left:49%;font-size:58px">🌿</span><span class="vine" style="left:71%;font-size:46px">🌿</span><span class="vine" style="left:93%;font-size:54px">🌿</span>${PLANTS}</div>
            <div class="cr-tree" aria-hidden="true"></div>
            <div class="cr-grid" id="cr-grid"></div>
            <svg class="cr-curve" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path id="cr-fill"/><path id="cr-line"/></svg>
            <div class="cr-rocket" id="cr-rocket" aria-hidden="true"><svg class="cr-ship" viewBox="-16 4 132 66" aria-hidden="true"><g class="cr-flame"><path d="M19 26 Q-2 22 -15 33 Q0 38 18 38 Z" fill="#ff8a1f"/><path d="M19 29 Q6 28 -4 33 Q6 35 18 36 Z" fill="#ffe27a"/></g><path d="M24 27 L7 12 L38 29 Z" fill="#e5484d"/><path d="M20 38 L5 56 L36 46 Z" fill="#b3261e"/><path d="M16 37 Q60 68 106 25 Q110 21 112 16 L108 15 Q104 20 100 22 Q60 41 19 25 Z" fill="#f7d038" stroke="#a87a00" stroke-width="1.5" stroke-linejoin="round"/><path d="M24 37 Q60 58 100 27" fill="none" stroke="#d9a916" stroke-width="2" stroke-linecap="round"/><path d="M104 26.5 Q110 21 112 16 L108 15 Q104 20 100 22 Z" fill="#5a3a12"/><path d="M16 37 L19 25 L23 27 L21 39 Z" fill="#5a3a12"/><image href="/assets/onkey-logo.png" x="40" y="12" width="36" height="21"/></svg><span class="cr-bang">💥</span></div>
            <div class="cr-center" id="cr-center" role="status" aria-live="off">${centerHtml(d)}</div>
          </div>
          <div class="felt-status cr-status" id="cr-status" role="status" aria-live="polite">${statusHtml(d)}</div>
          <div class="felt-controls cr-controls" id="cr-controls">${controlsHtml(d)}</div>
        </section>
        <aside class="casino-side" id="cr-side">${sideHtml(d)}</aside>
      </div>
    </div>`;
  }
  function refresh() {
    const d = data;
    if (!d || !$('#cr-root')) return;
    const sky = $('#cr-sky');
    const moon = d.phase === 'crashed' && d.crash >= d.max_mult;
    sky.className = `cr-sky ${d.phase}${moon ? ' moon' : ''}${mine(d) && mine(d).cashout != null ? ' safe' : ''}`;
    C.patch($('#cr-history'), historyHtml(d));
    C.patch($('#cr-center'), centerHtml(d));
    C.patch($('#cr-status'), statusHtml(d));
    C.patch($('#cr-controls'), controlsHtml(d));
    C.patch($('#cr-side'), sideHtml(d));
    paint();
    if (d.phase === 'flying') animate();
  }

  // ---- your moves ----------------------------------------------------------------------------------------------------
  async function post(path, body) {
    busy = true; error = '';
    refresh();
    try {
      const v = await api(path, { method: 'POST', body: JSON.stringify(body) });
      busy = false;
      apply(v);
    } catch (e) {
      busy = false; error = e.message;
      try { apply(await api(url())); } catch (_) { refresh(); }
    }
    loadMe?.();
  }
  function bet() {
    const d = data;
    if (!canBet() || autoBad(d)) return;
    if (d.phase !== 'betting') { queued = true; error = ''; refresh(); return; } // in the air: it goes in when the pad opens
    C.sound('chips');
    post('/api/crash/bet', { stake, auto: autoValue(d), request_id: C.ref() });
  }
  function cashOut() {
    if (canCash()) post('/api/crash/cashout', {});
  }

  function bind(viewEl) {
    const root = viewEl.querySelector('#cr-root');
    if (!root) return;
    C.bindSpeaker(root);
    refresh();
    root.addEventListener('click', (e) => {
      const t = e.target.closest('button');
      if (!t || t.disabled) return;
      if (t.dataset.crStake) { stake = +t.dataset.crStake; store('fs.crStake', String(stake)); error = ''; refresh(); }
      else if (t.hasAttribute('data-cr-bet')) bet();
      else if (t.hasAttribute('data-cr-unqueue')) { queued = false; refresh(); }
      else if (t.hasAttribute('data-cr-cash')) cashOut();
    });
    root.addEventListener('input', (e) => {
      if (e.target.id !== 'cr-auto') return;
      auto = e.target.value.trim();
      store('fs.crAuto', auto);
      // Only the field's own look and the Bet button change: the field keeps its caret.
      const d = data;
      e.target.closest('.cr-auto')?.classList.toggle('bad', autoBad(d));
      const go = $('#cr-controls [data-cr-bet]');
      if (go) go.disabled = busy || d.me.balance < stake || autoBad(d);
      const el = $('#cr-controls'); if (el) el._html = null; // its HTML no longer matches what was patched in
    });
    if (!loop?.running) startLoop();
  }

  return { init, load, view, bind };
})();
