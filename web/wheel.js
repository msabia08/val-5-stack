/* 5-Stack Tracker: the daily wheel (the Casino's Daily wheel page; data from /api/wheel, spins from /api/wheel/spin, both
 * in fivestack/wheel.py).
 *
 * One spin a day per bettor, resetting at midnight Pacific (3 AM Eastern); unlimited in demo mode. Each slice's size is
 * its chance, so the jackpot is a thin gold sliver (marked with a glowing badge on the rim). The server picks the
 * slice; the page turns the wheel to it with requestAnimationFrame so it always knows the angle: the pointer ticks as
 * each peg passes, and near the end the wheel may tease: it teeters on the boundary next to a big prize and then either
 * tips into it or falls back (TEASE). Sounds are synthesised with Web Audio (mute in the header, `fs.wheelMuted`), and
 * each prize has its own screen effect (`celebrate()`), biggest for the jackpot. Reduced motion skips the turn and the
 * flashes, shakes and confetti, keeping the sound and the result.
 *
 * FiveWheel.init(ctx) gets app.js's helpers; load(), view() and bind() are called like the other pages'.
 * FiveWheel.spin('jackpot') asks for a slice by key, which the server honours in demo mode only (for trying it out).
 * Plain JS, no dependencies; loaded before app.js.
 */
window.FiveWheel = (() => {
  'use strict';

  let state, $, $$, api, draw, esc, fmt, loadMe, confetti, plainName, toast;
  let data = null, owner = undefined;
  let angle = 0; // the wheel's rotation in degrees, kept across redraws so it never jumps back
  let spinning = false, result = null, error = '';
  let audio = null, muted = false;
  try { muted = localStorage.getItem('fs.wheelMuted') === '1'; } catch (e) { /* no storage: sound on */ }
  const ICONS = { credits: '🪙', bananas: '🍌', boost: '⚡', insurance: '🛡️', nothing: '🐒', again: '🔁', item: '🎁', jackpot: '🌟' };
  const BIG = new Set(['jackpot', 'item', 'c400']); // slices worth teasing toward
  const BULBS = 48;
  const R = 186; // the slices' radius in the SVG's 400 × 400 frame

  function init(ctx) { ({ state, $, $$, api, draw, esc, fmt, loadMe, confetti, plainName, toast } = ctx); }

  async function load() {
    const name = state.me?.name || null;
    const next = await api('/api/wheel');
    if (name !== owner) { result = null; error = ''; }
    owner = name; data = next;
  }

  const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const mod = (a, n) => ((a % n) + n) % n;
  const chance = (w) => {
    const p = w / data.total_weight;
    return p >= 0.1 ? fmt.pct(p) : `1 in ${Math.round(1 / p)}`;
  };

  // Each slice's start and end, in degrees clockwise from the top.
  function slices() {
    let at = 0;
    return data.segments.map((s, i) => {
      const span = (s.weight / data.total_weight) * 360;
      const out = { ...s, i, a0: at, a1: at + span, span };
      at += span;
      return out;
    });
  }
  // The slice under the pointer at a rotation: the wheel turns clockwise, so the pointer reads the angle -rot.
  function sliceAt(rot) {
    const w = mod(-rot, 360);
    return slices().find((s) => w >= s.a0 && w < s.a1) || slices()[0];
  }

  function point(r, deg) {
    const rad = ((deg - 90) * Math.PI) / 180;
    return [200 + r * Math.cos(rad), 200 + r * Math.sin(rad)];
  }

  // ---- drawing ------------------------------------------------------------------------------------------------------
  function wheelSvg() {
    const all = slices();
    const arc = (s, r) => {
      const [x0, y0] = point(r, s.a0), [x1, y1] = point(r, s.a1);
      return `M200 200 L${x0.toFixed(2)} ${y0.toFixed(2)} A${r} ${r} 0 ${s.span > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)} Z`;
    };
    const parts = all.map((s) => {
      const mid = (s.a0 + s.a1) / 2;
      const path = `<path class="wheel-slice wk-${esc(s.kind)}${s.i % 2 ? ' alt' : ''}" d="${arc(s, R)}"><title>${esc(s.label)}: ${chance(s.weight)}</title></path>`;
      let text = '';
      if (s.span >= 17) { // room for the icon and the label along the radius
        const [ix, iy] = point(R - 16, mid);
        // The label runs along the slice's middle, reading outward on the right half and inward on the left, so it
        // reads the right way up at rest.
        const right = mid < 180, r = 110;
        text = `<text class="wheel-icon" x="${ix.toFixed(1)}" y="${iy.toFixed(1)}" transform="rotate(${mid.toFixed(1)} ${ix.toFixed(1)} ${iy.toFixed(1)})">${ICONS[s.kind] || ''}</text>` +
          `<text class="wheel-label" x="${(right ? 200 + r : 200 - r).toFixed(1)}" y="200" transform="rotate(${(right ? mid - 90 : mid + 90).toFixed(2)} 200 200)">${esc(s.label)}</text>`;
      } else if (s.kind !== 'jackpot') {
        const [ix, iy] = point(R - 22, mid);
        text = `<text class="wheel-icon small" x="${ix.toFixed(1)}" y="${iy.toFixed(1)}" transform="rotate(${mid.toFixed(1)} ${ix.toFixed(1)} ${iy.toFixed(1)})">${ICONS[s.kind] || ''}</text>`;
      }
      return [path, text];
    });
    // The jackpot: a glowing gold sliver with a white-hot line down its middle and a halo either side, turning with
    // the wheel. The sliver itself stays its true size.
    const jp = all.find((s) => s.kind === 'jackpot');
    let jackpot = '';
    if (jp) {
      const mid = (jp.a0 + jp.a1) / 2, [lx, ly] = point(R - 2, mid), [hx, hy] = point(70, mid);
      jackpot = `<path class="wheel-jp-halo" d="${arc({ ...jp, a0: jp.a0 - 4, a1: jp.a1 + 4, span: jp.span + 8 }, R)}"/>` +
        `<path class="wheel-jp" d="${arc(jp, R)}"/>` +
        `<line class="wheel-jp-core" x1="${hx.toFixed(1)}" y1="${hy.toFixed(1)}" x2="${lx.toFixed(1)}" y2="${ly.toFixed(1)}"/>`;
    }
    // Pegs sit on the wheel's edge at each boundary, half on the slices and half on the rim; the flapper's tip just reaches them.
    const pegs = all.map((s) => { const [x, y] = point(R, s.a0); return `<circle class="wheel-peg" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.2"/>`; }).join('');
    const bulbs = Array.from({ length: BULBS }, (_, k) => {
      if (k === 0) return ''; // none behind the flapper at the top
      const [x, y] = point(R + 15, (k * 360) / BULBS); // on the rim's outer border
      return `<circle class="wheel-bulb" style="--k:${k % 6};--c:var(--bulb-${(k % 3) + 1})" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="4.6"/>`;
    }).join('');
    return `<svg class="wheel-svg" viewBox="-36 -36 472 472" role="img" aria-label="The prize wheel">` +
      '<defs><radialGradient id="wheel-gold" cx="200" cy="200" r="190" gradientUnits="userSpaceOnUse">' +
      '<stop offset="0" stop-color="#fff6c2"/><stop offset=".55" stop-color="#ffd54a"/><stop offset="1" stop-color="#ff9f1c"/></radialGradient>' +
      '<filter id="wheel-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="4"/></filter></defs>' +
      `<circle class="wheel-rim" cx="200" cy="200" r="${R + 15}"/>` +
      `<g class="wheel-bulbs">${bulbs}</g>` +
      `<g id="wheel-rotor" style="transform: rotate(${angle}deg)">${parts.map((x) => x[0]).join('')}${jackpot}${pegs}${parts.map((x) => x[1]).join('')}</g>` +
      '<circle class="wheel-hub" cx="200" cy="200" r="58"/></svg>';
  }

  function countdown() {
    const s = Math.max(0, Math.round(data.next_reset - Date.now() / 1000));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? `${h}h ${m}m` : `${m}m`;
  }

  function resultLine() {
    if (error) return `<p class="wheel-error" role="alert">${esc(error)}</p>`;
    if (spinning) return '<p class="wheel-result muted">Spinning…</p>';
    if (!result) return '';
    const what = {
      credits: `You won ${esc(result.label)}.`,
      bananas: `You won ${esc(result.label)}. They're in your banana wallet.`,
      boost: `Boost token! Your next single of up to ${fmt.credits(data.token.max_stake)} credits pays ${fmt.pct(data.token.boost)} more profit.`,
      insurance: `Insurance token! If your next single loses, you get the stake back (up to ${fmt.credits(data.token.max_stake)} credits).`,
      nothing: 'Onkey ate it. Nothing this time.',
      again: 'Spin again!',
      item: `${esc(result.label)}. Wear it from <a href="#shop">Onkey's Shop</a>.`,
      jackpot: `JACKPOT! You won ${fmt.credits(result.amount || 0)} credits.`,
    }[data.segments[result.segment]?.kind] || esc(result.label);
    return `<p class="wheel-result${result.prize === 'jackpot' ? ' jackpot' : ''}">${what}</p>`;
  }

  function spinArea() {
    const me = data.me;
    if (!me) return '<button class="btn wheel-spin" data-signin>Sign in to spin</button>';
    if (spinning) return '<button class="btn wheel-spin" disabled>Spinning…</button>';
    if (me.spins_left > 0) return `<button class="btn wheel-spin" id="wheel-spin">Spin</button>${data.unlimited ? '<p class="muted small">Demo mode: spin as often as you like.</p>' : ''}`;
    return `<button class="btn wheel-spin" disabled>Spun today</button><p class="muted small">Next spin in ${countdown()}, at midnight Pacific (3 AM Eastern).</p>`;
  }

  function prizes() {
    const rows = slices().slice().sort((a, b) => a.weight - b.weight).map((s) =>
      `<tr><th scope="row"><span class="wheel-key wk-${esc(s.kind)}" aria-hidden="true"></span>${ICONS[s.kind] || ''} ${esc(s.label)}</th><td class="num">${chance(s.weight)}</td></tr>`).join('');
    return `<section class="card"><h2>Prizes</h2><p class="muted small">Rarest first. A slice's size on the wheel is its chance.</p>` +
      `<table class="compact wheel-prizes"><tbody>${rows}</tbody></table>` +
      `<details class="how"><summary>Where the prizes come from</summary><div class="how-body">Credits and insurance refunds are on the house, free; only the jackpot slice is paid from the house's money: it's the whole progressive jackpot, which grows with every bet and spin. Bananas go to your banana wallet for Onkey's Shop. A free cosmetic is one you don't own yet (100 bananas if you own them all). A boost token gives your next single of up to ${fmt.credits(data.token.max_stake)} credits ${fmt.pct(data.token.boost)} more profit (not on top of the odds boost of the game); an insurance token refunds your next single if it loses, up to ${fmt.credits(data.token.max_stake)} credits. Tokens are used by your next single bet, never a parlay. The wheel resets at midnight Pacific, which is 3 AM Eastern. Where the wheel stops is decided before it starts turning; the slow finish is only for show.</div></details></section>`;
  }

  function tokens() {
    const me = data.me;
    if (!me) return '';
    const list = me.perks.map((p) => `<li>${ICONS[p.kind]} <b>${p.kind === 'boost' ? 'Boost token' : 'Insurance token'}</b> <span class="muted small">${p.kind === 'boost'
      ? `+${fmt.pct(data.token.boost)} profit on your next single up to ${fmt.credits(data.token.max_stake)} credits` : `stake back if your next single loses, up to ${fmt.credits(data.token.max_stake)}`}</span></li>`).join('');
    return `<section class="card"><h2>Your tokens</h2>${list ? `<ul class="wheel-tokens">${list}</ul>` : '<p class="muted small">None waiting. Win one on the wheel and your next single uses it.</p>'}</section>`;
  }

  function spinList(title, rows, empty, who) {
    const items = rows.map((r) => `<li>${who ? `<span>${plainName(r.bettor)}</span>` : ''}<span>${ICONS[data.segments[r.segment]?.kind] || ''} ${esc(r.label || '')}</span>` +
      `<span class="muted small">${fmt.date(r.created_ts * 1000)}</span></li>`).join('');
    return `<section class="card"><h2>${title}</h2>${items ? `<ul class="wheel-spins${who ? ' who' : ''}">${items}</ul>` : `<p class="muted small">${empty}</p>`}</section>`;
  }

  const speaker = () => `<button type="button" class="btn icon wheel-sound" id="wheel-sound" aria-pressed="${!muted}" aria-label="${muted ? 'Turn wheel sound on' : 'Turn wheel sound off'}" title="${muted ? 'Sound off' : 'Sound on'}">` +
    `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M4 9h4l5-4v14l-5-4H4z" fill="currentColor"/>${muted
      ? '<path d="M16 9l5 6M21 9l-5 6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
      : '<path d="M16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/>'}</svg></button>`;

  function view() {
    if (!data || (state.me?.name || null) !== owner) {
      load().then(() => { if (state.view === 'wheel') draw(); }).catch(() => {
        const el = $('#wheel-loading'); if (el) el.textContent = 'Could not load the wheel. Open this tab again to retry.';
      });
      return '<section class="card" id="wheel-loading">Loading the wheel…</section>';
    }
    return `<div class="wheel-layout"><section class="card wheel-stage">
        <div class="wheel-head"><div><h2>Daily wheel</h2><p class="muted small">One free spin a day. The biggest prize is the whole jackpot.</p></div>${speaker()}</div>
        <div class="wheel-row"><div class="wheel-box"><div class="wheel-pivot" aria-hidden="true"></div><div class="wheel-pointer" aria-hidden="true" style="${pointerStyle()}"></div>${wheelSvg()}
          <div class="wheel-center"><span>Jackpot</span><b>${fmt.credits(data.jackpot)}</b></div></div>
        <div class="wheel-controls"><div aria-live="polite" id="wheel-live">${resultLine()}</div><div id="wheel-act">${spinArea()}</div></div></div>
      </section>
      <div class="wheel-side">${prizes()}${tokens()}</div></div>
      <div class="grid-2">${spinList('Latest spins', data.recent, 'Nobody has spun yet.', true)}${data.me ? spinList('Your spins', data.me.history, 'Your spins will show here.', false) : ''}</div>`;
  }

  // ---- sound --------------------------------------------------------------------------------------------------------
  // Everything is synthesised: tones through a gain envelope, and filtered noise for clicks, whooshes and cymbals.
  function ctx() {
    if (muted) return null;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      if (audio.state === 'suspended') audio.resume();
    } catch (e) { audio = null; }
    return audio;
  }
  function tone(freq, start, dur, { type = 'sine', vol = 0.18, to = null, attack = 0.005 } = {}) {
    const a = ctx(); if (!a) return;
    const t = a.currentTime + start, o = a.createOscillator(), g = a.createGain();
    o.type = type; o.frequency.setValueAtTime(freq, t);
    if (to) o.frequency.exponentialRampToValueAtTime(to, t + dur);
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(vol, t + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g).connect(a.destination); o.start(t); o.stop(t + dur + 0.05);
  }
  let noiseBuf = null;
  function noise(start, dur, { freq = 2000, q = 1, vol = 0.2, type = 'bandpass', to = null } = {}) {
    const a = ctx(); if (!a) return;
    if (!noiseBuf) {
      noiseBuf = a.createBuffer(1, a.sampleRate, a.sampleRate);
      const ch = noiseBuf.getChannelData(0);
      for (let i = 0; i < ch.length; i++) ch[i] = Math.random() * 2 - 1;
    }
    const t = a.currentTime + start, src = a.createBufferSource(), f = a.createBiquadFilter(), g = a.createGain();
    src.buffer = noiseBuf; f.type = type; f.frequency.setValueAtTime(freq, t); f.Q.value = q;
    if (to) f.frequency.exponentialRampToValueAtTime(to, t + dur);
    g.gain.setValueAtTime(vol, t); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    src.connect(f).connect(g).connect(a.destination); src.start(t); src.stop(t + dur + 0.05);
  }
  let lastTick = 0, droneNode = null;
  const sfx = {
    windup: () => noise(0, 0.5, { freq: 300, to: 2400, q: 0.8, vol: 0.12 }),
    tick: (fast) => {
      const now = performance.now();
      if (now - lastTick < 38) return; // at full speed the pegs blur into a rattle, not a buzz
      lastTick = now;
      noise(0, 0.03, { freq: 3200, q: 4, vol: fast ? 0.1 : 0.22 });
      tone(fast ? 1500 : 1100, 0, 0.03, { type: 'square', vol: fast ? 0.025 : 0.05 });
    },
    beat: () => { tone(70, 0, 0.18, { vol: 0.45, to: 45 }); tone(70, 0.2, 0.16, { vol: 0.3, to: 45 }); },
    creak: () => tone(220, 0, 0.5, { type: 'sawtooth', vol: 0.03, to: 330 }),
    snap: () => { noise(0, 0.06, { freq: 4000, q: 2, vol: 0.35 }); tone(90, 0, 0.25, { vol: 0.4, to: 50 }); },
    drone: (on) => {
      const a = on ? ctx() : audio;
      if (!a) return;
      if (on && !droneNode) {
        const o1 = a.createOscillator(), o2 = a.createOscillator(), f = a.createBiquadFilter(), g = a.createGain();
        o1.type = o2.type = 'sawtooth'; o1.frequency.value = 110; o2.frequency.value = 110.9;
        o1.frequency.linearRampToValueAtTime(165, a.currentTime + 3); o2.frequency.linearRampToValueAtTime(166.2, a.currentTime + 3);
        f.type = 'lowpass'; f.frequency.value = 500; f.frequency.linearRampToValueAtTime(1600, a.currentTime + 3);
        g.gain.setValueAtTime(0.0001, a.currentTime); g.gain.exponentialRampToValueAtTime(0.07, a.currentTime + 0.4);
        o1.connect(f); o2.connect(f); f.connect(g).connect(a.destination); o1.start(); o2.start();
        droneNode = { o1, o2, g };
      } else if (!on && droneNode) {
        const { o1, o2, g } = droneNode, t = a.currentTime;
        g.gain.cancelScheduledValues(t); g.gain.setValueAtTime(g.gain.value, t); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.25);
        o1.stop(t + 0.3); o2.stop(t + 0.3); droneNode = null;
      }
    },
    teaseWin: () => [523, 659, 784, 1047, 1319].forEach((f, i) => tone(f, i * 0.07, 0.35, { type: 'triangle', vol: 0.16 })),
    teaseLose: () => [392, 370, 349, 294].forEach((f, i) => tone(f, i * 0.28, i === 3 ? 0.9 : 0.3, { type: 'sawtooth', vol: 0.07 })),
    credits: (big) => { tone(988, 0, 0.12, { type: 'square', vol: 0.07 }); tone(1319, 0.09, big ? 0.5 : 0.35, { type: 'square', vol: 0.07 });
      if (big) [1568, 2093].forEach((f, i) => tone(f, 0.25 + i * 0.09, 0.4, { type: 'square', vol: 0.05 })); },
    bananas: () => { tone(300, 0, 0.35, { vol: 0.25, to: 700 }); tone(500, 0.18, 0.3, { vol: 0.18, to: 1000 }); },
    boost: () => { tone(200, 0, 0.6, { type: 'sawtooth', vol: 0.08, to: 1400 }); noise(0, 0.6, { freq: 600, to: 5000, vol: 0.08 }); },
    insurance: () => [523, 784, 1047].forEach((f) => tone(f, 0, 0.9, { type: 'triangle', vol: 0.09 })),
    item: () => [523, 659, 784, 1047, 784, 1047].forEach((f, i) => tone(f, i * 0.11, 0.3, { type: 'triangle', vol: 0.14 })),
    again: () => { tone(1400, 0, 0.35, { type: 'sawtooth', vol: 0.05, to: 300 }); tone(880, 0.38, 0.3, { type: 'triangle', vol: 0.15 }); },
    nothing: () => { [392, 370, 349, 330].forEach((f, i) => tone(f, i * 0.3, i === 3 ? 1 : 0.32, { type: 'sawtooth', vol: 0.06 })); noise(0.05, 0.12, { freq: 900, vol: 0.15 }); },
    jackpot: () => {
      const chords = [[523, 659, 784], [587, 740, 880], [659, 831, 988], [784, 988, 1175, 1568]];
      chords.forEach((c, i) => c.forEach((f) => tone(f, i * 0.32, i === 3 ? 2 : 0.34, { type: i % 2 ? 'square' : 'triangle', vol: 0.06 })));
      noise(0.96, 1.6, { freq: 7000, type: 'highpass', vol: 0.18 });
      [1568, 1760, 2093, 2349, 2637, 3136].forEach((f, i) => tone(f, 1.3 + i * 0.08, 0.25, { type: 'square', vol: 0.03 }));
    },
  };

  // ---- motion -------------------------------------------------------------------------------------------------------
  // The spin is a small physics simulation, run in full before the wheel moves and then played back frame by frame.
  // The wheel has momentum and slows from air drag and rolling friction. Each peg has to push the pointer (a sprung
  // flapper) aside to get past: the push is hardest halfway up and fades to nothing at the top, so a slow wheel creeps
  // over a peg, and one without quite enough momentum stops on the way up and the flapper pushes it back. The flapper
  // itself is a damped spring: pegs bend it, and it snaps back and wobbles when one slips past. The server has already
  // picked the slice; `solve()` searches for the throw speed that makes the simulated wheel come to rest in it, and for a
  // tease it aims to arrive at the deciding peg with barely enough momentum to get over it, or barely too little. Units
  // are degrees and milliseconds.
  const PHYS = {
    Z: 1.2, // degrees of wheel travel a peg spends pushing the flapper aside
    K: 4e-6, // the flapper's resistance halfway up a peg, deg/ms²
    BACK: 0.6, // the share of that it pushes back with (the rubber soaks up the rest)
    MU: 6e-7, // rolling friction, deg/ms²
    DRAG: 1 / 900, // air drag, per ms
    MAXP: 10.5, KP: 0.0032, CP: 0.034, // the flapper: its bend at the top of a peg (enough to clear it, its tip reaching past the pegs), its spring and damping
  };
  const WIND = 5, WIND_MS = 300, THROW_MS = 110; // the hand pulls the wheel back 5°, then throws it
  const MIN_TRAVEL = 1500; // at least four turns
  const TENSE_V = 0.012; // below this speed (12° a second) a tease starts to build
  function hermite(p0, p1, v0, v1, d) {
    return (u) => {
      const u2 = u * u, u3 = u2 * u;
      return (2 * u3 - 3 * u2 + 1) * p0 + (u3 - 2 * u2 + u) * d * v0 + (-2 * u3 + 3 * u2) * p1 + (u3 - u2) * d * v1;
    };
  }
  // A rotation, at least `min`, that puts the pointer on wheel angle w.
  const rotFor = (w, min) => { const r0 = -w; return r0 + 360 * Math.ceil((min - r0) / 360); };
  const throwFrom = (start, v0) => start - WIND + (v0 * THROW_MS) / 2; // where the throw hands over to the simulation

  // One spin from `pos0` at speed `v0`. With `rec`, the rotation and the flapper's angle every millisecond and each peg
  // passing, until the flapper has settled after the wheel stops.
  function simulate(all, pos0, v0, rec) {
    const find = (w) => all.find((x) => w >= x.a0 && w < x.a1) || all[all.length - 1];
    let pos = pos0, v = v0, s = find(mod(-pos, 360)), peak = pos;
    let ahead = false, behind = false, phi = 0, phiV = 0, stopped = false, stopT = 0, slowT = null, turnT = null;
    const track = rec ? [] : null, ptr = rec ? [] : null, ticks = rec ? [] : null;
    for (let t = 0; t < 30000; t++) {
      const w = mod(-pos, 360), dA = w - s.a0, dB = s.a1 - w;
      const fa = dA < PHYS.Z ? 1 - dA / PHYS.Z : 0, fb = dB < PHYS.Z ? 1 - dB / PHYS.Z : 0;
      // The peg ahead pushes back on a wheel moving forward (and keeps pressing while it rolls back off it); the peg
      // behind only touches the flapper when the wheel rolls backward into it.
      let F = 0;
      if (fa > 0 && (v > 0 || ahead)) { ahead = true; F -= (v >= 0 ? 1 : PHYS.BACK) * PHYS.K * Math.sin(Math.PI * fa); } else if (fa === 0) ahead = false;
      if (fb > 0 && (v < 0 || behind)) { behind = true; F += (v <= 0 ? 1 : PHYS.BACK) * PHYS.K * Math.sin(Math.PI * fb); } else if (fb === 0) behind = false;
      if (!stopped) {
        const nv = v + F - Math.sign(v) * PHYS.MU - PHYS.DRAG * v;
        if ((v > 0 && nv <= 0) || (v < 0 && nv >= 0)) { // it stops here, unless a peg is pushing hard enough to roll it back
          if (turnT === null && v > 0) turnT = t;
          if (Math.abs(F) <= PHYS.MU) { v = 0; stopped = true; stopT = t; } else v = nv;
        } else v = nv;
        pos += v;
        if (pos > peak) peak = pos;
        if (slowT === null && v < TENSE_V) slowT = t;
        const ns = find(mod(-pos, 360));
        if (ns !== s) { if (rec) ticks.push({ t, v }); s = ns; ahead = false; behind = false; }
      }
      phiV += -PHYS.KP * phi - PHYS.CP * phiV; phi += phiV;
      if (ahead && phi > -PHYS.MAXP * fa) { phi = -PHYS.MAXP * fa; if (phiV > 0) phiV = 0; }
      if (behind && phi < PHYS.MAXP * fb) { phi = PHYS.MAXP * fb; if (phiV < 0) phiV = 0; }
      if (rec) { track.push(pos); ptr.push(phi); }
      if (stopped && (!rec || t > stopT + 900)) break;
    }
    return { rest: pos, peak, stopped, stopT, slowT, turnT, track, ptr, ticks, slice: find(mod(-pos, 360)) };
  }

  // The throw speed whose spin satisfies `pred` by the narrowest margin (pred false below it, true above).
  function bisect(pred) {
    let lo = 0.3, hi = 4;
    if (pred(lo) || !pred(hi)) return null;
    for (let k = 0; k < 60; k++) { const mid = (lo + hi) / 2; if (pred(mid)) hi = mid; else lo = mid; }
    return hi;
  }

  // A spin that ends in slice s, from rotation `start`. kind 'tip': it creeps over the peg into the slice at the last
  // moment; 'back': it climbs the peg out of the slice, can't make it, and rolls back; null: it just comes to rest.
  function solve(all, s, kind, start) {
    const runAt = (v0, rec) => simulate(all, throwFrom(start, v0), v0, rec);
    let v0, check;
    if (kind === 'tip') {
      const B = rotFor(s.a1, start + MIN_TRAVEL), depth = s.kind === 'jackpot' ? 0.35 : Math.min(s.span - PHYS.Z - 0.2, 0.25 + Math.random() * 0.25);
      v0 = bisect((v) => runAt(v).rest >= B + depth);
      check = (r) => r.slice.i === s.i;
    } else if (kind === 'back') {
      const A = rotFor(s.a0, start + MIN_TRAVEL);
      v0 = bisect((v) => runAt(v).peak >= A - PHYS.Z * (1 - 0.86)); // up to 86% of the way over
      check = (r) => r.slice.i === s.i && r.peak < A;
    } else {
      for (const frac of [0.5, 0.35, 0.65, 0.25, 0.75]) {
        const T = rotFor(s.a0 + s.span * frac, start + MIN_TRAVEL);
        v0 = bisect((v) => runAt(v).rest >= T);
        if (v0 === null) continue;
        const r = runAt(v0, true);
        if (r.stopped && r.slice.i === s.i) return { kind, v0, r, decisive: r.stopT };
      }
      return null;
    }
    if (v0 === null) return null;
    const r = runAt(v0, true);
    if (!r.stopped || !check(r)) return null;
    return { kind, v0, r, decisive: kind === 'tip' ? r.ticks[r.ticks.length - 1].t : r.turnT };
  }

  // How the spin ends, decided from the result alone, so teases happen on wins and misses alike.
  function plan(s, start) {
    const all = slices(), n = all.length, behind = all[(s.i + 1) % n], ahead = all[(s.i + n - 1) % n];
    let kind = null;
    if (s.kind === 'jackpot') kind = 'tip';
    else if (ahead.kind === 'jackpot' && Math.random() < 0.75) kind = 'back'; // right up to the jackpot, and back
    else if (behind.kind === 'jackpot' && Math.random() < 0.75) kind = 'tip'; // over the jackpot and just out of it
    else if (BIG.has(s.key) && Math.random() < 0.35) kind = 'tip';
    else if (BIG.has(ahead.key) && Math.random() < 0.25) kind = 'back';
    else if (BIG.has(behind.key) && Math.random() < 0.25) kind = 'tip';
    else if (Math.random() < 0.08) kind = Math.random() < 0.5 ? 'tip' : 'back';
    return (kind && solve(all, s, kind, start)) || solve(all, s, null, start);
  }

  // The flapper's bend for a wheel resting at `rot`: leaning on the peg ahead if it stopped against one.
  function restTilt(rot) {
    const w = mod(-rot, 360), s = sliceAt(rot), dA = w - s.a0;
    return dA < PHYS.Z ? -PHYS.MAXP * (1 - dA / PHYS.Z) : 0;
  }
  let pointerDeg = 0;
  const pointerStyle = () => `transform:translateX(-50%) rotate(${pointerDeg.toFixed(2)}deg)`;

  // Play a solved spin: the hand's pull and throw, then the simulation. `landed` runs as the wheel comes to rest, `done`
  // once the flapper has settled too.
  function play(p, rotor, start, landed, done) {
    const ptr = $('.wheel-pointer'), r = p.r, last = r.track.length - 1;
    const wind = hermite(start, start - WIND, 0, 0, WIND_MS), toss = hermite(start - WIND, throwFrom(start, p.v0), 0, p.v0, THROW_MS);
    const stage = $('.wheel-stage');
    const tense = (on) => { stage?.classList.toggle('wheel-tense', on); bulbs(on ? 'tease' : 'spin'); sfx.drone(on); };
    // A tease builds once the wheel is crawling (at most the last 2.5 s before the deciding moment) and ends there.
    const tenseFrom = p.kind ? Math.max(r.slowT ?? p.decisive, p.decisive - 2500) : Infinity;
    let t0 = performance.now(), tick = 0, tensed = false, beatAt = 0, hasLanded = false;
    sfx.windup();
    bulbs('spin');
    const frame = (now) => {
      const e = now - t0;
      let i = -1;
      if (e < WIND_MS) angle = wind(e / WIND_MS);
      else if (e < WIND_MS + THROW_MS) angle = toss((e - WIND_MS) / THROW_MS);
      else {
        i = Math.min(last, Math.floor(e - WIND_MS - THROW_MS));
        angle = r.track[i]; pointerDeg = r.ptr[i];
        while (tick < r.ticks.length && r.ticks[tick].t <= i) {
          const k = r.ticks[tick++];
          if (p.kind === 'tip' && k.t === p.decisive) sfx.snap(); else sfx.tick(Math.abs(k.v) > 0.25);
        }
        if (!tensed && i >= tenseFrom && i < p.decisive) { tensed = true; tense(true); sfx.creak(); beatAt = now; }
        if (tensed && now >= beatAt) { sfx.beat(); beatAt = now + 560; }
        if (tensed && i >= p.decisive) { tensed = false; tense(false); if (p.kind === 'back') sfx.snap(); }
        if (!hasLanded && i >= r.stopT) { hasLanded = true; if (tensed) { tensed = false; tense(false); } landed(); }
      }
      rotor.style.transform = `rotate(${angle}deg)`;
      if (ptr) ptr.style.transform = `translateX(-50%) rotate(${pointerDeg.toFixed(2)}deg)`;
      if (i >= last) { done(); return; }
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  }

  // ---- screen effects -----------------------------------------------------------------------------------------------
  function layer() {
    const el = document.createElement('div');
    el.className = 'wheel-fx-layer';
    el.setAttribute('aria-hidden', 'true');
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 6000);
    return el;
  }
  function flash(kind, times = 1) {
    if (reducedMotion()) return;
    const el = layer();
    el.innerHTML = `<div class="wheel-flash fx-${kind}" style="--times:${times}"></div>`;
  }
  function shake(px) {
    const box = $('.wheel-box');
    if (!box || reducedMotion()) return;
    box.style.setProperty('--shake', `${px}px`);
    box.classList.remove('shaking'); void box.offsetWidth; box.classList.add('shaking');
  }
  function bulbs(mode) { // 'spin' chases, 'win' flashes all, 'tease' pulses gold, '' at rest
    const g = $('.wheel-bulbs');
    if (g) g.setAttribute('class', `wheel-bulbs${mode ? ` ${mode}` : ''}`);
  }
  function banner(text, cls) {
    const el = layer();
    el.innerHTML = `<div class="wheel-banner ${cls}">${esc(text)}</div>`;
  }
  function rain(emoji, n) {
    if (reducedMotion()) return;
    const el = layer();
    el.innerHTML = Array.from({ length: n }, () => `<span class="wheel-rain" style="left:${(Math.random() * 100).toFixed(1)}vw;` +
      `animation-delay:${(Math.random() * 1.6).toFixed(2)}s;animation-duration:${(2 + Math.random() * 1.6).toFixed(2)}s;font-size:${18 + Math.round(Math.random() * 22)}px">${emoji[Math.floor(Math.random() * emoji.length)]}</span>`).join('');
  }

  // Each prize's moment: its sound, flash, confetti; the jackpot gets everything.
  function celebrate(s, r) {
    const box = $('.wheel-box'), still = reducedMotion();
    bulbs('win');
    setTimeout(() => bulbs(''), s.kind === 'jackpot' ? 5200 : 2400);
    const burst = (big, style) => { if (!still) confetti(box, big, style); };
    switch (s.kind) {
      case 'credits': sfx.credits(r.amount >= 200); flash('credits', r.amount >= 400 ? 2 : 1); burst(r.amount >= 400); if (r.amount >= 200) shake(r.amount >= 400 ? 8 : 5); break;
      case 'bananas': sfx.bananas(); flash('bananas'); burst(false, { emoji: ['🍌'] }); break;
      case 'boost': sfx.boost(); flash('boost', 2); burst(false, { emoji: ['⚡'] }); break;
      case 'insurance': sfx.insurance(); flash('insurance'); burst(false, { emoji: ['🛡️'] }); break;
      case 'item': sfx.item(); flash('item', 2); shake(6); burst(false); setTimeout(() => burst(false, { emoji: ['🎁', '✨'] }), 400); break;
      case 'again': sfx.again(); flash('again'); break;
      case 'nothing': {
        sfx.nothing();
        const stage = $('.wheel-stage');
        if (stage && !still) { stage.classList.add('wheel-sad'); setTimeout(() => stage.classList.remove('wheel-sad'), 1800); }
        if (!still) { const el = layer(); el.innerHTML = '<div class="wheel-nom">🐒<span>nom</span></div>'; }
        break;
      }
      case 'jackpot': {
        sfx.jackpot();
        document.documentElement.classList.add('wheel-dim');
        setTimeout(() => document.documentElement.classList.remove('wheel-dim'), 4200);
        flash('jackpot', 3); shake(12);
        banner(`JACKPOT! +${fmt.credits(r.amount || 0)}`, 'jackpot');
        [0, 450, 900, 1500, 2100].forEach((t) => setTimeout(() => burst(true), t));
        rain(['🌟', '🪙', '⭐'], 60);
        break;
      }
      default: break;
    }
  }

  // ---- spinning -----------------------------------------------------------------------------------------------------
  async function spin(force) {
    if (spinning || !data.me) return;
    error = ''; result = null; spinning = true;
    const live = $('#wheel-live'), act = $('#wheel-act');
    if (live) live.innerHTML = resultLine();
    if (act) act.innerHTML = spinArea();
    ctx(); // start audio on the click itself, so the browser allows it
    let r;
    try {
      r = await api('/api/wheel/spin', { method: 'POST', body: JSON.stringify(typeof force === 'string' ? { segment: force } : {}) });
    } catch (e) {
      spinning = false; error = e.message; draw(); return;
    }
    const s = slices()[r.segment], rotor = $('#wheel-rotor');
    const finish = async (after) => {
      spinning = false; result = r;
      bulbs('');
      celebrate(s, r);
      try { await Promise.all([load(), loadMe(), after]); } catch (e) { /* the result is already shown */ }
      pointerDeg = restTilt(angle);
      draw();
    };
    if (!rotor || reducedMotion()) {
      angle = rotFor(s.a0 + s.span / 2, angle + 360);
      if (rotor) rotor.style.transform = `rotate(${angle}deg)`;
      finish();
      return;
    }
    rotor.style.transition = 'none';
    const start = angle, p = plan(s, start);
    if (!p) { // can't happen with the wheel's slices, but never leave a spin hanging
      angle = rotFor(s.a0 + s.span / 2, start + 360); rotor.style.transform = `rotate(${angle}deg)`; finish(); return;
    }
    let settled = null;
    const flapperDone = new Promise((res) => { settled = res; });
    play(p, rotor, start, () => finish(flapperDone), settled);
  }

  function bindSound() {
    $('#wheel-sound')?.addEventListener('click', () => {
      muted = !muted;
      try { localStorage.setItem('fs.wheelMuted', muted ? '1' : '0'); } catch (e) { /* not remembered */ }
      if (muted) sfx.drone(false);
      const b = $('#wheel-sound'); if (b) b.outerHTML = speaker();
      bindSound();
    });
  }

  function bind(viewEl) {
    $('#wheel-spin', viewEl)?.addEventListener('click', () => spin());
    bindSound();
  }

  return { init, load, view, bind, spin };
})();
