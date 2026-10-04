/* Roulette on Onkey's wheel (fivestack/roulette.py): a solo table you spin when you like, and one shared table everyone
   bets on together before a clock spins it. The server takes the bets, draws the number and settles; this page draws
   the wheel and the betting layout, stages your chips, sends them, and rolls the ball to the pocket the server picked.
   The zero is the banana pocket. Onkey (FiveCasino) is the croupier.

   Chips: click a spot on the layout to put the selected chip there (`staged`, yours and not sent yet); right-click
   takes that spot's chips off. At the solo table Spin sends them and spins; at the shared table Place bets puts them
   down for the round (they can't be taken back) and the table's clock spins. Nothing gives the result away before the
   ball has landed: the status line, the side cards, Onkey and the balance in the top bar all wait for it. */
window.FiveRoulette = (() => {
  'use strict';
  let state, $, $$, api, draw, esc, fmt, loadMe, confetti, plainName, holdBalance, releaseBalance;
  const C = window.FiveCasino;
  let which = (() => { try { return localStorage.getItem('fs.rlTable') === 'shared' ? 'shared' : 'solo'; } catch (_) { return 'solo'; } })();
  const data = { solo: null, shared: null };
  const seen = { solo: null, shared: null }; // the last log entry Onkey reacted to, per table
  let chip = (() => { try { return +(localStorage.getItem('fs.rlChip') || 10); } catch (_) { return 10; } })();
  let staged = {}; // your chips on the layout, not sent yet: {bet key: credits}
  let trail = []; // the order they went down in, for Undo
  let busy = false, error = '', loop = null, owner;
  const shown = { solo: null, shared: null }; // the last result the page has revealed, per table (a `last` or a round)
  // The spin being played: {table, id, number, t0, T, w0}. The wheel's angle and the ball's place are functions of
  // time, so a redraw mid-spin picks up where it was.
  let spin = null;
  let wheelDeg = 0, ballAt = null; // at rest: the wheel's angle, and the pocket the ball sits in (null: no ball)
  let raf = 0;

  // The croupier's lines. Onkey talks about himself as Onkey.
  const LINES = {
    spin: ['No more bets!', 'Round and round. Onkey loves this part.', 'The ball is away!', 'Hands off the table. It\'s rolling.'],
    number: ['{n}, {colour}.', '{colour} {n}.', 'It\'s {n}. {colour}.'],
    banana: ['BANANA! Onkey\'s favourite pocket.', 'The banana! Onkey knew it. Onkey always knows.', 'Banana pocket. Everything else goes to Onkey.'],
    win: ['{name} wins {amount}!', '{amount} to {name}. Onkey pays, slowly.', 'A winner: {name}, {amount}.'],
    straight: ['Straight up on {n}! {amount} to {name}!', '{name} called {n} exactly. Onkey is suspicious.'],
    banana_win: ['{name} bet on the banana and the banana came. {amount}!', 'On the banana! {amount} to {name}. Onkey is proud.'],
    lose: ['The house thanks you.', 'Not this time. The wheel has no memory.', 'Onkey sweeps the chips. Gently.'],
    bet: ['Chips down from {name}.', '{name} is in.'],
  };
  const pick = (list) => list[Math.floor(Math.random() * list.length)];
  const fill = (line, vars) => line.replace(/\{(\w+)\}/g, (_, k) => (vars[k] != null ? vars[k] : ''));
  const say = (kind, vars = {}, as = kind) => C.say($('#rl-onkey'), as, vars, fill(pick(LINES[kind]), vars));

  function init(ctx) { ({ state, $, $$, api, draw, esc, fmt, loadMe, confetti, plainName, holdBalance, releaseBalance } = ctx); }

  const url = (since) => `/api/roulette?table=${which}${since != null ? `&since=${since}` : ''}`;
  async function load() {
    const name = state.me?.name || null;
    if (owner !== name) { owner = name; data.solo = data.shared = null; seen.solo = seen.shared = null; shown.solo = shown.shared = null; staged = {}; trail = []; }
    apply(await api(url()), true);
  }

  // ---- what's on the table ----------------------------------------------------------------------------------------
  const total = (bets) => Object.values(bets || {}).reduce((a, b) => a + b, 0);
  const down = () => (data[which]?.me?.down || 0); // already placed this round (the shared table)
  const colourOf = (d, n) => (n === d.banana ? 'banana' : d.reds.includes(n) ? 'red' : 'black');
  const numLabel = (d, n) => (n === d.banana ? '🍌' : String(n));
  // The result the page may show for this table: nothing while its spin is still being played.
  const spinning = (t = which) => !!spin && spin.table === t && !spin.landed; // once it lands the wheel only coasts
  function revealed(d) {
    const t = d.table === 'shared' ? 'shared' : 'solo';
    return spinning(t) ? null : shown[t];
  }

  // A fresh table state from the server.
  function apply(d, first) {
    const t = d.table === 'shared' ? 'shared' : 'solo';
    const old = data[t];
    if (old && d.version < old.version && t === 'shared') return; // an older answer arriving late
    data[t] = d;
    C.syncClock(d.server_time);
    C.absorb(d.looks);
    const last = d.me && d.me.last;
    if (t === 'solo') {
      if (last && (!shown.solo || shown.solo.id !== last.id)) {
        if (first || !old) { shown.solo = { ...last }; ballAt = last.number; } // just opened: show it where it lies
        else startSpin(t, `s${last.id}`, last.number, 6.2, { ...last });
      }
    } else if (d.phase === 'spinning' && (!spin || spin.id !== `r${d.round}`) && (!shown.shared || shown.shared.round !== d.round)) {
      const left = d.spin_until ? C.secondsLeft(d.spin_until) : 0;
      if (left > 2.5) startSpin(t, `r${d.round}`, d.result, Math.min(7, left - 1), null);
    } else if (d.phase === 'done' && !spinning('shared')) {
      // The ball has landed (or the page was opened after it did): now the server says who won what.
      const waiting = shown.shared && shown.shared.round === d.round && shown.shared.net == null;
      const isNew = !shown.shared || shown.shared.round !== d.round;
      shown.shared = sharedResult(d);
      ballAt = d.result;
      if (waiting && !isNew && t === which && $('#rl-root')) announce(d, shown.shared, d.result);
    } else if (d.phase === 'betting' && shown.shared && d.round !== shown.shared.round && !d.seats.length) {
      ballAt = shown.shared.number; // the next round: the ball stays where it fell
    }
    const fresh = (d.log || []).filter((e) => seen[t] != null && e.id > seen[t]);
    if (d.log && d.log.length) seen[t] = d.log[d.log.length - 1].id; else if (seen[t] == null) seen[t] = 0;
    if (t === which && state.view === 'roulette' && $('#rl-root')) {
      refresh();
      if (!first) fresh.filter((e) => e.kind === 'bet' && e.bettor !== d.me?.name).slice(-1).forEach((e) => say('bet', { name: e.bettor }));
    }
    if (t === 'shared' && which === 'shared' && !loop?.running && state.view === 'roulette') startLoop();
  }
  const sharedResult = (d) => {
    const mine = d.seats.find((s) => d.me && s.bettor === d.me.name);
    return { round: d.round, number: d.result, colour: d.colour, staked: mine ? mine.total : 0, net: mine ? mine.net : null,
      won: mine ? mine.won : [], bets: mine ? mine.bets : {}, seats: d.seats };
  };
  function startLoop() {
    loop?.stop();
    loop = C.live({ alive: () => state.view === 'roulette' && which === 'shared', fetchNext: (v) => api(url(v)),
      apply: (d) => apply(d), version: () => data.shared?.version ?? 0 });
  }

  // ---- the wheel ----------------------------------------------------------------------------------------------------
  const R_OUT = 150, R_NUM = 128, R_IN = 106, R_TRACK = 141, R_POCKET = 117, R_HUB = 62;
  const pt = (r, deg) => { const a = (deg - 90) * Math.PI / 180; return [160 + r * Math.cos(a), 160 + r * Math.sin(a)]; };
  function wheelSvg(d) {
    const n = d.wheel.length, step = 360 / n;
    const pockets = d.wheel.map((num, i) => {
      const a0 = (i - 0.5) * step, a1 = (i + 0.5) * step;
      const [x0, y0] = pt(R_OUT, a0), [x1, y1] = pt(R_OUT, a1), [x2, y2] = pt(R_IN, a1), [x3, y3] = pt(R_IN, a0);
      const [tx, ty] = pt(R_NUM, i * step);
      return `<path class="rl-pocket ${colourOf(d, num)}" d="M${x0.toFixed(1)} ${y0.toFixed(1)} A${R_OUT} ${R_OUT} 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)} L${x2.toFixed(1)} ${y2.toFixed(1)} A${R_IN} ${R_IN} 0 0 0 ${x3.toFixed(1)} ${y3.toFixed(1)} Z"/>` +
        `<text class="rl-pocket-n" x="${tx.toFixed(1)}" y="${ty.toFixed(1)}" transform="rotate(${(i * step).toFixed(2)} ${tx.toFixed(1)} ${ty.toFixed(1)})">${num === d.banana ? '🍌' : num}</text>`;
    }).join('');
    return `<svg class="rl-wheel" id="rl-wheel" viewBox="0 0 320 320" role="img" aria-label="The roulette wheel">
      <circle class="rl-bowl" cx="160" cy="160" r="158"/>
      <g id="rl-rotor">${pockets}<circle class="rl-cone" cx="160" cy="160" r="${R_IN}"/><circle class="rl-hub" cx="160" cy="160" r="${R_HUB}"/>
        <image href="/assets/onkey-logo.png" x="${160 - 44}" y="${160 - 26}" width="88" height="51"/></g>
      <circle class="rl-ball" id="rl-ball" cx="160" cy="160" r="6.5"/>
    </svg>`;
  }
  const bounce = (u) => { // ease-out with bounces: the ball rattling down into its pocket
    const n1 = 7.5625, d1 = 2.75;
    if (u < 1 / d1) return n1 * u * u;
    if (u < 2 / d1) { u -= 1.5 / d1; return n1 * u * u + 0.75; }
    if (u < 2.5 / d1) { u -= 2.25 / d1; return n1 * u * u + 0.9375; }
    u -= 2.625 / d1; return n1 * u * u + 0.984375;
  };
  const WHEEL_SPEED = 55, COAST_S = 2.2; // degrees a second while the ball rolls; then it coasts to a stop
  // The wheel's angle and the ball's (angle, radius) `t` seconds into a spin lasting T, landing on pocket index `i`.
  function pose(s, t, d) {
    const step = 360 / d.wheel.length, i = d.wheel.indexOf(s.number);
    const over = Math.max(0, t - s.T), c = Math.min(over, COAST_S);
    const w = s.w0 + WHEEL_SPEED * Math.min(t, s.T) + WHEEL_SPEED * (c - (c * c) / (2 * COAST_S));
    const k = Math.min(1, t / s.T);
    const rel = i * step + 360 * 5.5 * Math.pow(1 - k, 2.4); // in the wheel's frame: it runs the other way and slows into the pocket
    const u = Math.max(0, Math.min(1, (k - 0.6) / 0.34));
    return { w, a: w + rel, r: R_TRACK + (R_POCKET - R_TRACK) * bounce(u) };
  }
  function place(w, ball) {
    const rotor = $('#rl-rotor'), b = $('#rl-ball');
    if (rotor) rotor.setAttribute('transform', `rotate(${(w % 360).toFixed(2)} 160 160)`);
    if (!b) return;
    if (!ball) { b.setAttribute('visibility', 'hidden'); return; }
    const [x, y] = pt(ball.r, ball.a);
    b.setAttribute('visibility', 'visible'); b.setAttribute('cx', x.toFixed(1)); b.setAttribute('cy', y.toFixed(1));
  }
  // The wheel as it rests: the ball, if any, sitting in the pocket it fell in.
  function rest() {
    const d = data[which];
    if (!d) return;
    const i = ballAt == null ? -1 : d.wheel.indexOf(ballAt);
    place(wheelDeg, i < 0 ? null : { a: wheelDeg + i * (360 / d.wheel.length), r: R_POCKET });
  }
  function startSpin(table, id, number, T, result) {
    if (spin && spin.id === id) return;
    spin = { table, id, number, T, w0: wheelDeg, t0: performance.now(), result, clack: 0, landed: false };
    ballAt = null;
    // The shared table is paid as the ball is let go: keep the balance in the top bar as it was until it lands.
    if (table === 'shared' && state.me) holdBalance?.(state.me.balance);
    if (table === which && $('#rl-root')) say('spin');
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(frame);
    if (table === which) refresh();
  }
  function frame(now) {
    const s = spin, d = s && data[s.table];
    if (!s || !d) return;
    const t = (now - s.t0) / 1000, p = pose(s, t, d);
    if (s.table === which) {
      place(p.w, { a: p.a, r: p.r });
      const gap = 0.06 + 0.5 * Math.pow(Math.min(1, t / s.T), 2); // the ball's rattle slows with it
      if (t < s.T && t - s.clack > gap) { s.clack = t; C.sound('card'); }
    }
    if (t >= s.T && !s.landed) { s.landed = true; landed(s, d); }
    if (t >= s.T + COAST_S) { wheelDeg = p.w % 360; spin = null; if (s.table === which) { rest(); refresh(); } return; }
    wheelDeg = p.w % 360;
    raf = requestAnimationFrame(frame);
  }
  // The ball is in its pocket: now the result can be shown, the balance released, and Onkey says it.
  function landed(s, d) {
    ballAt = s.number;
    const cur = data[s.table];
    shown[s.table] = s.result || (cur && cur.phase !== 'betting' && cur.round === +s.id.slice(1) ? sharedResult(cur) : { round: +s.id.slice(1), number: s.number, colour: colourOf(d, s.number), net: null, won: [], bets: {}, staked: 0, seats: [] });
    releaseBalance?.();
    loadMe?.();
    if (s.table !== which || state.view !== 'roulette') return;
    refresh();
    announce(d, shown[s.table], s.number);
  }
  // Onkey calls the number, and the win when there is one.
  function announce(d, res, number) {
    const vars = { n: numLabel(d, number), colour: colourOf(d, number), name: d.me?.name || 'you', amount: fmt.credits(Math.max(0, res.net || 0)) };
    const s = { number };
    if (res.net > 0) {
      C.sound('win');
      const straight = (res.won || []).some((k) => d.bets[k] && d.bets[k].kind === 'straight');
      say(s.number === d.banana && straight ? 'banana_win' : straight ? 'straight' : 'win', vars, 'win');
      const board = $('#rl-board');
      if (board && confetti) confetti(board, straight);
    } else if (s.number === d.banana) say('banana', vars, 'banana');
    else if (res.net != null && res.net < 0 && Math.random() < 0.5) say('lose', vars, 'lose');
    else say('number', vars, 'deal');
  }

  // ---- the betting layout --------------------------------------------------------------------------------------------
  const CW = 60, CH = 56, X0 = 60, GH = CH * 3, BW = X0 + 12 * CW + 60, BH = GH + 44 + 44;
  const cell = (n) => ({ x: X0 + Math.floor((n - 1) / 3) * CW, y: (2 - ((n - 1) % 3)) * CH });
  const EVEN_ORDER = ['low', 'even', 'red', 'black', 'odd', 'high'];
  // Where a bet sits on the layout (its chips go there, and its click zone is centred there).
  function anchor(key, b) {
    const ns = b.numbers;
    if (b.kind === 'straight') return ns[0] === 0 ? { x: X0 / 2, y: GH / 2 } : { x: cell(ns[0]).x + CW / 2, y: cell(ns[0]).y + CH / 2 };
    if (b.kind === 'split') {
      if (ns[0] === 0) return { x: X0, y: cell(ns[1]).y + CH / 2 };
      const c = cell(ns[0]);
      return ns[1] === ns[0] + 1 ? { x: c.x + CW / 2, y: c.y } : { x: c.x + CW, y: c.y + CH / 2 };
    }
    if (b.kind === 'corner') return ns[0] === 0 ? { x: X0, y: GH } : { x: cell(ns[0]).x + CW, y: cell(ns[0]).y };
    if (b.kind === 'street') return ns[0] === 0 ? { x: X0, y: ns[2] === 2 ? CH * 2 : CH } : { x: cell(ns[0]).x + CW / 2, y: GH };
    if (b.kind === 'six') return { x: cell(ns[0]).x + CW, y: GH };
    if (b.kind === 'column') return { x: X0 + 12 * CW + 30, y: (3 - +key.split(':')[1]) * CH + CH / 2 };
    if (b.kind === 'dozen') return { x: X0 + (+key.split(':')[1] - 1) * 4 * CW + 2 * CW, y: GH + 22 };
    return { x: X0 + EVEN_ORDER.indexOf(key) * 2 * CW + CW, y: GH + 44 + 22 };
  }
  const ZONE_ORDER = ['even', 'dozen', 'column', 'straight', 'split', 'street', 'six', 'corner']; // later ones sit on top
  function boardSvg(d) {
    const res = revealed(d), hit = res ? res.number : null;
    const mine = { ...(d.me?.bets || {}) }; // placed (the shared table)
    const all = {};
    Object.entries(mine).forEach(([k, v]) => { all[k] = (all[k] || 0) + v; });
    Object.entries(staged).forEach(([k, v]) => { all[k] = (all[k] || 0) + v; });
    const won = new Set(res ? res.won || [] : []);
    const cells = [`<g class="rl-cell banana${hit === d.banana ? ' hit' : ''}" data-n="0"><path d="M${X0} 0 H18 Q2 0 2 16 V${GH - 16} Q2 ${GH} 18 ${GH} H${X0} Z"/><text x="${X0 / 2}" y="${GH / 2}">🍌</text></g>`];
    for (let n = 1; n <= 36; n++) {
      const c = cell(n);
      cells.push(`<g class="rl-cell ${colourOf(d, n)}${hit === n ? ' hit' : ''}" data-n="${n}"><rect x="${c.x}" y="${c.y}" width="${CW}" height="${CH}"/><text x="${c.x + CW / 2}" y="${c.y + CH / 2}">${n}</text></g>`);
    }
    const box = (x, y, w, h, label, cls = '') => `<g class="rl-cell outside ${cls}"><rect x="${x}" y="${y}" width="${w}" height="${h}"/><text x="${x + w / 2}" y="${y + h / 2}">${label}</text></g>`;
    for (let i = 1; i <= 3; i++) {
      cells.push(box(X0 + 12 * CW, (3 - i) * CH, 60, CH, '2 to 1'));
      cells.push(box(X0 + (i - 1) * 4 * CW, GH, 4 * CW, 44, d.bets[`dozen:${i}`].label));
    }
    EVEN_ORDER.forEach((k, i) => cells.push(box(X0 + i * 2 * CW, GH + 44, 2 * CW, 44, d.bets[k].label, k === 'red' || k === 'black' ? k : '')));
    const keys = Object.keys(d.bets).sort((a, b) => ZONE_ORDER.indexOf(d.bets[a].kind) - ZONE_ORDER.indexOf(d.bets[b].kind));
    const zones = keys.map((k) => {
      const b = d.bets[k], a = anchor(k, b), tip = `${b.label}: pays ${b.pays} to 1`;
      const attrs = `class="rl-zone" data-key="${esc(k)}" data-ns="${b.numbers.join(',')}"`;
      if (b.kind === 'straight') return b.numbers[0] === 0 ? `<rect ${attrs} x="2" y="0" width="${X0 - 2}" height="${GH}"><title>${tip}</title></rect>`
        : `<rect ${attrs} x="${a.x - CW / 2}" y="${a.y - CH / 2}" width="${CW}" height="${CH}"><title>${tip}</title></rect>`;
      if (b.kind === 'column') return `<rect ${attrs} x="${a.x - 30}" y="${a.y - CH / 2}" width="60" height="${CH}"><title>${tip}</title></rect>`;
      if (b.kind === 'dozen') return `<rect ${attrs} x="${a.x - 2 * CW}" y="${GH}" width="${4 * CW}" height="44"><title>${tip}</title></rect>`;
      if (b.kind === 'even') return `<rect ${attrs} x="${a.x - CW}" y="${GH + 44}" width="${2 * CW}" height="44"><title>${tip}</title></rect>`;
      return `<circle ${attrs} cx="${a.x}" cy="${a.y}" r="10"><title>${tip}</title></circle>`;
    }).join('');
    // Everyone else's chips at the shared table, small and to one side; then yours.
    const others = [];
    if (d.table === 'shared') {
      d.seats.filter((s) => !d.me || s.bettor !== d.me.name).forEach((s, i) => Object.entries(s.bets).forEach(([k, v]) => {
        const a = anchor(k, d.bets[k]);
        others.push(`<g class="rl-chip other" transform="translate(${a.x + 13 + (i % 3) * 4}, ${a.y + 11})"><circle r="8"/><title>${esc(s.bettor)}: ${v} on ${esc(d.bets[k].label)}</title></g>`);
      }));
    }
    const chips = Object.entries(all).map(([k, v]) => {
      const a = anchor(k, d.bets[k]), isStaged = staged[k] > 0;
      return `<g class="rl-chip${isStaged ? ' staged' : ' placed'}${won.has(k) ? ' won' : ''}" transform="translate(${a.x}, ${a.y})"><circle r="13"/><text>${v}</text></g>`;
    }).join('');
    return `<svg class="rl-layout" viewBox="-2 -2 ${BW + 4} ${BH + 4}" role="group" aria-label="The betting layout">${cells.join('')}${zones}${others.join('')}${chips}</svg>`;
  }

  // ---- the page ----------------------------------------------------------------------------------------------------
  function readout(d) {
    const res = revealed(d);
    const big = spinning() ? '<div class="rl-number rolling">…</div>'
      : res ? `<div class="rl-number ${res.colour || colourOf(d, res.number)}">${numLabel(d, res.number)}</div>` : '<div class="rl-number none">–</div>';
    // The newest number is already in the server's history while the ball is still rolling here: leave it out.
    const hist = (d.history || []).filter((h, i) => !(spinning() && i === 0 && (d.table === 'solo' || d.phase === 'done')));
    return `${big}<div class="rl-history" aria-label="Earlier numbers">${hist.slice(0, 12).map((h) => `<span class="${h.colour}">${numLabel(d, h.number)}</span>`).join('') || '<span class="muted small">No spins yet</span>'}</div>`;
  }
  function statusHtml(d) {
    if (error) return `<span class="casino-error">${esc(error)}</span>`;
    if (!d.me) return '<a href="#" data-signin>Sign in</a> to play.';
    const res = revealed(d);
    if (spinning()) return 'No more bets. The ball is rolling…';
    if (d.table === 'shared' && d.phase === 'betting') {
      if (d.deadline) return `Bets close in ${C.countdown(d.deadline)} ${C.timer(d.deadline, d.bet_window_s)}`;
      return 'Put chips down to open the round. The wheel spins 20 seconds after the first bet.';
    }
    if (res && res.net != null && (d.table === 'solo' || d.phase !== 'betting')) {
      const what = `${numLabel(d, res.number)}${res.number === d.banana ? ' (the banana)' : `, ${res.colour}`}`;
      if (!res.staked) return `${what}. You sat this one out.`;
      return res.net > 0 ? `${what}. <b class="up">You won ${fmt.credits(res.net)} credits.</b>` : res.net === 0 ? `${what}. You broke even.` : `${what}. <span class="down">You lost ${fmt.credits(-res.net)} credits.</span>`;
    }
    // The shared table between the ball landing here and the server saying who won what.
    if (d.table === 'shared' && d.phase !== 'betting') return res ? `${numLabel(d, res.number)}${res.number === d.banana ? ' (the banana)' : `, ${res.colour}`}. Onkey is counting the chips…` : 'No more bets.';
    if (d.table === 'shared') return total(staged) ? 'Chips are ready. Place them before the clock runs out.' : 'Pick a chip, then click the layout to bet.';
    return total(staged) ? 'Chips are down. Spin when you\'re ready.' : 'Pick a chip, then click the layout to bet.';
  }
  function controlsHtml(d) {
    if (!d.me) return '';
    const sum = total(staged);
    const lock = spinning() || busy || (d.table === 'shared' && d.phase !== 'betting');
    const chips = d.stakes.map((s) => `<button type="button" class="rl-pick${s === chip ? ' on' : ''}" data-rl-chip="${s}" aria-pressed="${s === chip}" aria-label="${s} credit chip">${C.chip(s, { cls: 'mini' })}</button>`).join('');
    const go = d.table === 'solo'
      ? `<button class="btn primary rl-go" data-rl-spin ${lock || !sum ? 'disabled' : ''}>Spin</button>`
      : `<button class="btn primary rl-go" data-rl-place ${lock || !sum ? 'disabled' : ''}>Place bets</button>`;
    return `<div class="rl-chips" role="group" aria-label="Chip">${chips}</div>
      <div class="rl-total"><span>On the table</span><b>${fmt.credits(sum + down())}</b><span class="muted small">of ${fmt.credits(d.table_max)}</span></div>
      <button class="btn ghost small" data-rl-undo ${lock || !trail.length ? 'disabled' : ''}>Undo</button>
      <button class="btn ghost small" data-rl-clear ${lock || !sum ? 'disabled' : ''}>Clear</button>${go}`;
  }
  function sideHtml(d) {
    const res = revealed(d);
    const lastCard = res && res.staked ? `<section class="card"><h2>Your last spin</h2>
        <p class="rl-last"><span class="rl-dot ${res.colour || colourOf(d, res.number)}">${numLabel(d, res.number)}</span>
        <b class="${res.net > 0 ? 'up' : res.net < 0 ? 'down' : ''}">${res.net > 0 ? '+' : ''}${fmt.credits(res.net)}</b> <span class="muted small">on ${fmt.credits(res.staked)} staked</span></p>
        <ul class="rl-bets">${Object.entries(res.bets || {}).map(([k, v]) => `<li class="${(res.won || []).includes(k) ? 'won' : ''}"><span>${esc(d.bets[k]?.label || k)}</span><span class="num">${fmt.credits(v)}</span></li>`).join('')}</ul></section>` : '';
    const seats = d.table === 'shared' ? `<section class="card"><h2>At the table</h2>${d.seats.length
      ? `<ul class="rl-bets">${d.seats.map((s) => `<li><span>${C.who(s.bettor)}</span><span class="num">${fmt.credits(s.total)}${s.net != null && !spinning() ? ` <b class="${s.net > 0 ? 'up' : s.net < 0 ? 'down' : ''}">${s.net > 0 ? '+' : ''}${fmt.credits(s.net)}</b>` : ''}</span></li>`).join('')}</ul>`
      : '<p class="muted small">Nobody has chips down yet.</p>'}</section>` : '';
    const s = d.me && d.me.season;
    const season = s ? `<section class="card"><h2>Your season</h2><div class="rl-season">
        <div><span class="tile-label">Spins</span><b>${fmt.n0(s.spins)}</b></div>
        <div><span class="tile-label">Net</span><b class="${s.net > 0 ? 'up' : s.net < 0 ? 'down' : ''}">${s.net > 0 ? '+' : ''}${fmt.credits(s.net)}</b></div>
        <div><span class="tile-label">Best spin</span><b>${s.best > 0 ? `+${fmt.credits(s.best)}` : '–'}</b></div>
        <div><span class="tile-label">Bananas</span><b>${fmt.n0(s.bananas)}</b></div></div></section>` : '';
    const pays = [['Banana, straight', d.banana_pays], ['A number', 35], ['Split (2)', 17], ['Street (3)', 11], ['Corner (4)', 8], ['Six-line (6)', 5], ['Dozen or column', 2], ['Red, black, odd, even, 1-18, 19-36', 1]];
    const rules = `<section class="card"><h2>Payouts</h2><table class="compact rl-pays"><tbody>${pays.map(([a, b]) => `<tr><th scope="row">${a}</th><td class="num">${b} to 1</td></tr>`).join('')}</tbody></table>
      <details class="how"><summary>The odds</summary><div class="how-body">Onkey's wheel has the numbers 1 to 36 and one banana pocket, 37 in all. Every bet keeps the same share for the house, 1 in 37 (${fmt.pct(d.edge)}), except a straight bet on the banana: it pays ${d.banana_pays} to 1, which is exactly fair. The banana beats every bet that doesn't cover it. You can put up to ${fmt.credits(d.table_max)} credits on a spin. The number is drawn the moment the ball is let go; the roll is only for show.</div></details></section>`;
    return `${lastCard}${seats}${rules}${season}`;
  }

  function view() {
    const d = data[which];
    if (!d || (d.me?.name || null) !== (state.me?.name || null)) {
      load().then(() => { if (state.view === 'roulette') draw(); }).catch((e) => {
        const el = $('#rl-loading'); if (el) el.textContent = `Could not load the table: ${e.message}`;
      });
      return '<section class="card" id="rl-loading">Polishing the wheel…</section>';
    }
    const others = data.shared ? data.shared.seats.length : 0;
    return `<div id="rl-root" class="casino-page">
      <div class="casino-bar">
        <div class="seg" role="tablist" aria-label="Table">
          <button class="seg-btn ${which === 'solo' ? 'on' : ''}" role="tab" aria-selected="${which === 'solo'}" data-rl-table="solo">Solo table</button>
          <button class="seg-btn ${which === 'shared' ? 'on' : ''}" role="tab" aria-selected="${which === 'shared'}" data-rl-table="shared">Shared table${others ? ` <span class="seg-count">${others}</span>` : ''}</button>
        </div>
        <span class="muted small">${which === 'solo' ? 'Just you and Onkey. The wheel waits for you.' : 'Everyone bets on the same spin. It goes 20 seconds after the first chip.'}</span>
        ${C.speaker()}
      </div>
      <div class="casino-layout">
        <section class="felt rl-felt" aria-label="Roulette table">
          <div class="rl-top">${C.dealer('rl-onkey')}${wheelSvg(d)}<div class="rl-readout" id="rl-readout">${readout(d)}</div></div>
          <div class="rl-board" id="rl-board">${boardSvg(d)}</div>
          <div class="felt-status" id="rl-status" role="status" aria-live="polite">${statusHtml(d)}</div>
          <div class="felt-controls rl-controls" id="rl-controls">${controlsHtml(d)}</div>
        </section>
        <aside class="casino-side" id="rl-side">${sideHtml(d)}</aside>
      </div>
    </div>`;
  }
  function refresh() {
    const d = data[which];
    if (!d || !$('#rl-root')) return;
    C.patch($('#rl-readout'), readout(d));
    C.patch($('#rl-board'), boardSvg(d));
    C.patch($('#rl-status'), statusHtml(d));
    C.patch($('#rl-controls'), controlsHtml(d));
    C.patch($('#rl-side'), sideHtml(d));
    if (!spin || spin.table !== which) rest();
  }

  // ---- your moves ----------------------------------------------------------------------------------------------------
  const locked = () => { const d = data[which]; return !d || !d.me || busy || spinning() || (d.table === 'shared' && d.phase !== 'betting'); };
  function add(key) {
    const d = data[which];
    if (locked() || !d.bets[key]) return;
    if (total(staged) + down() + chip > d.table_max) { error = `The table takes up to ${fmt.credits(d.table_max)} credits a spin.`; refresh(); return; }
    if (total(staged) + chip > d.me.balance) { error = 'Not enough credits for another chip.'; refresh(); return; }
    error = '';
    staged[key] = (staged[key] || 0) + chip;
    trail.push([key, chip]);
    C.sound('chips');
    refresh();
  }
  function take(key) {
    if (locked() || !staged[key]) return;
    delete staged[key];
    trail = trail.filter(([k]) => k !== key);
    error = '';
    refresh();
  }
  async function send(path) {
    const d = data[which];
    if (locked() || !total(staged)) return;
    busy = true; error = '';
    const bets = { ...staged }, sum = total(bets);
    refresh();
    // The server pays before the ball lands: hold the balance in the top bar (less the stake) until it does.
    if (state.me) holdBalance?.(Math.max(0, state.me.balance - sum));
    try {
      const v = await api(path, { method: 'POST', body: JSON.stringify({ bets, request_id: C.ref() }) });
      if (which === 'shared') { staged = {}; trail = []; }
      busy = false;
      apply(v);
      if (which === 'shared') loadMe?.();
    } catch (e) {
      busy = false; error = e.message;
      releaseBalance?.();
      try { apply(await api(url())); } catch (_) { refresh(); }
    }
    if (d.table === 'shared') releaseBalance?.(); // the chips are down: the balance shows them gone; the spin holds it again
  }
  function hover(ns) {
    $$('#rl-board .rl-cell.lit').forEach((el) => el.classList.remove('lit'));
    (ns || []).forEach((n) => $(`#rl-board .rl-cell[data-n="${n}"]`)?.classList.add('lit'));
  }

  function bind(viewEl) {
    const root = viewEl.querySelector('#rl-root');
    if (!root) return;
    C.bindSpeaker(root);
    if (spin && spin.table === which) { cancelAnimationFrame(raf); raf = requestAnimationFrame(frame); } else rest();
    root.addEventListener('click', (e) => {
      const zone = e.target.closest('.rl-zone');
      if (zone) { add(zone.dataset.key); return; }
      const t = e.target.closest('button');
      if (!t || t.disabled) return;
      if (t.dataset.rlTable && t.dataset.rlTable !== which) {
        which = t.dataset.rlTable; error = ''; staged = {}; trail = [];
        try { localStorage.setItem('fs.rlTable', which); } catch (_) { /* unavailable */ }
        loop?.stop(); loop = null;
        ballAt = shown[which] ? shown[which].number : null;
        draw();
      } else if (t.dataset.rlChip) {
        chip = +t.dataset.rlChip;
        try { localStorage.setItem('fs.rlChip', String(chip)); } catch (_) { /* unavailable */ }
        refresh();
      } else if (t.hasAttribute('data-rl-undo')) {
        const lastChip = trail.pop();
        if (lastChip) { staged[lastChip[0]] -= lastChip[1]; if (staged[lastChip[0]] <= 0) delete staged[lastChip[0]]; }
        error = ''; refresh();
      } else if (t.hasAttribute('data-rl-clear')) {
        staged = {}; trail = []; error = ''; refresh();
      } else if (t.hasAttribute('data-rl-spin')) send('/api/roulette/spin');
      else if (t.hasAttribute('data-rl-place')) send('/api/roulette/bet');
    });
    root.addEventListener('contextmenu', (e) => {
      const zone = e.target.closest('.rl-zone');
      if (zone) { e.preventDefault(); take(zone.dataset.key); }
    });
    root.addEventListener('mouseover', (e) => {
      const zone = e.target.closest('.rl-zone');
      hover(zone ? zone.dataset.ns.split(',') : null);
    });
    if (which === 'shared' && !loop?.running) startLoop();
  }

  return { init, load, view, bind };
})();
