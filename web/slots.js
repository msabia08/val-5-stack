/* Server-settled slots. Reel motion is cosmetic; only the server chooses the result. */
window.FiveSlots = (() => {
  'use strict';
  let state, $, $$, api, draw, esc, fmt, loadMe, confetti, plainName;
  let data = null, owner = null, stake = 10, busy = false, result = null, error = '', pending = null;
  let motion = null, audio = null, muted = localStorage.getItem('fs.slotsMuted') === '1';
  // Wins on these symbols get bigger celebrations, 1 to 4 (celebrate()); other wins just light the cabinet.
  const FX = { diamond: 1, banana: 2, monkey: 3, golden: 4 };
  // The tease: when the first two reels match, the third may keep spinning, glow, then creep up to the payline so you
  // can't tell whether it will land. The chance depends only on the pair (never on whether the third reel wins, so a
  // tease gives nothing away) and grows with what the pair could pay. TEASE_LEVEL (0-4) sets how long and slow it is.
  const TEASE = { cherry: .15, bell: .2, spike: .35, diamond: .55, banana: .75, monkey: .9, golden: 1 };
  const TEASE_LEVEL = { cherry: 0, bell: 0, spike: 1, diamond: 2, banana: 3, monkey: 4, golden: 4 };
  // Stops slow down for bigger symbols: the wait (ms) before the next reel grows with the symbol the last one showed,
  // longer still after a matching pair, so they follow what's on screen and come on losses too. A long opening spin
  // (BIG_OPENING) comes with every banana, Onkey or Golden Onkey win and one spin in ten besides, so it's no giveaway.
  const PAUSE = { cherry: 0, bell: 60, spike: 150, diamond: 300, banana: 450, monkey: 650, golden: 900 };
  const BIG_OPENING = ['banana', 'monkey', 'golden'];
  const storageKey = (name) => `fs.slotSpin.${name.toLowerCase()}`;
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  function init(ctx) { ({ state, $, $$, api, draw, esc, fmt, loadMe, confetti, plainName } = ctx); }
  function syncOwner() {
    const name = state.me?.name || null;
    if (owner === name) return;
    motion?.cancel();
    owner = name; data = null; result = null; error = ''; pending = null;
    try { pending = name ? JSON.parse(sessionStorage.getItem(storageKey(name))) : null; } catch (_) { /* unavailable */ }
    if (pending) stake = pending.stake;
  }
  async function load() {
    syncOwner();
    const name = owner, next = await api('/api/slots');
    if (owner === name) data = next;
  }
  function remember(value) {
    pending = value;
    try {
      if (value) sessionStorage.setItem(storageKey(owner), JSON.stringify(value));
      else sessionStorage.removeItem(storageKey(owner));
    } catch (_) { /* the in-memory reference still protects retries */ }
  }
  const money = (n) => fmt.credits(n);
  const signed = (n) => `${n > 0 ? '+' : ''}${money(n)}`;
  // A symbol as drawn: its picture (Onkey; the Golden Onkey is Onkey with a golden glow) or its emoji.
  const glyph = (i) => {
    const s = data.symbols[i];
    return s.img ? `<img class="slots-img${s.glow ? ' golden' : ''}" src="${esc(s.img)}" alt="" draggable="false">` : s.icon;
  };
  const icon = (i) => `<span role="img" aria-label="${esc(data.symbols[i].name)}">${glyph(i)}</span>`;
  const reelsNow = () => result?.reels || [0, 3, 5];
  // The LED frame: lights at fixed spots on a rounded outline 8px inside the cabinet's edge, clockwise from the top
  // left: the straight runs end where the corners curve (20px round at the top, 12px at the bottom, following the
  // cabinet's 28px and 20px corners), with a light on each top curve. Each carries its place in a repeating run of 9
  // (--k), which staggers the CSS chase so the light travels round; the lights never move.
  const LEDS = (() => {
    const out = [], TOP = 62, SIDE = 40, arc = (8 + 20 - 20 * Math.SQRT1_2).toFixed(2);
    const span = (from, to, n, k) => `calc(${from}px + (100% - ${from + to}px) * ${(k / (n - 1)).toFixed(4)})`;
    const put = (x, y) => out.push(`<i style="left:${x};top:${y};--k:${8 - (out.length % 9)}"></i>`);
    for (let k = 0; k < TOP; k++) put(span(28, 28, TOP, k), '8px');
    put(`calc(100% - ${arc}px)`, `${arc}px`);
    for (let k = 0; k < SIDE; k++) put('calc(100% - 8px)', span(28, 20, SIDE, k));
    for (let k = 0; k < TOP; k++) put(span(20, 20, TOP, TOP - 1 - k), 'calc(100% - 8px)');
    for (let k = 0; k < SIDE; k++) put('8px', span(28, 20, SIDE, SIDE - 1 - k));
    put(`${arc}px`, `${arc}px`);
    return out.join('');
  })();
  // The sound toggle's speaker, crossed out when muted.
  const speaker = (off) => `<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M4 9h4l5-4v14l-5-4H4z" fill="currentColor"/>${off
    ? '<path d="M16 9.5l5 5M21 9.5l-5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
    : '<path d="M16 8.8a4.5 4.5 0 0 1 0 6.4M18.6 6.2a8.2 8.2 0 0 1 0 11.6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/>'}</svg>`;
  const soundLabel = () => (muted ? 'Turn sound on' : 'Mute sound');
  // Each reel's strip matches the machine's display weights ("show", which losing spins are drawn from), reduced to
  // whole cells (10 cherries, 4 Onkeys in 42), spread evenly, with each reel's spread shifted so the three strips
  // differ. Reel motion is in cells.
  // A secret symbol (the Golden Onkey) isn't on the strips at all: when a reel stops on it, it takes over one cell ("swapped"),
  // put in just before it rolls into view and taken out once it has rolled away on the next spin.
  let strips = null, stripsFor = '', rest = [null, null, null], swapped = [null, null, null];
  function stripsNow() {
    const show = data.machines.find((g) => g.key === 'jackpot').show, key = show.join();
    if (key !== stripsFor) {
      const gcd = (a, b) => (b ? gcd(b, a % b) : a);
      const unit = show.filter((w, s) => !data.symbols[s].secret).reduce(gcd);
      const weights = show.map((w) => Math.round(w / unit));
      strips = [0, 1, 2].map((r) => weights
        .flatMap((w, s) => (data.symbols[s].secret ? [] : Array.from({ length: w }, (_, k) => ({ s, at: (k + (((s + 1) * 0.6180339 + r * 0.3819661) % 1)) / w }))))
        .sort((a, b) => a.at - b.at || a.s - b.s).map((c) => c.s));
      stripsFor = key; rest = [null, null, null]; swapped = [null, null, null];
    }
    return strips;
  }
  const cells = () => stripsNow()[0].length;
  const wrap = (n) => ((n % cells()) + cells()) % cells();
  // The cell a reel shows for a symbol: its swapped-in cell, else where it last stopped if that's the symbol, else the
  // symbol's first cell. A secret symbol with no cell yet (reduced motion skips the roll) takes over the resting cell.
  function cellFor(i, value) {
    const strip = stripsNow()[i];
    if (swapped[i]?.value === value) return swapped[i].cell;
    if (strip.includes(value)) {
      if (!busy) swapped[i] = null;
      return strip[rest[i]] === value ? rest[i] : strip.indexOf(value);
    }
    if (busy) return rest[i] ?? 0;
    swapped[i] = { cell: rest[i] ?? 0, value };
    return swapped[i].cell;
  }
  // Put a symbol in a cell of a reel on the page (all three copies), for a swap in or out.
  function setCell(i, cell, value) {
    const strip = $$('.slots-strip')[i], n = cells();
    if (strip) [cell, n + cell, 2 * n + cell].forEach((c) => { strip.children[c].innerHTML = glyph(value); });
  }
  const tierOf = (spin) => spin && spin.net > 0 && spin.reels.every((r) => r === spin.reels[0]) ? FX[data.symbols[spin.reels[0]]?.key] || 0 : 0;
  // Three copies make the wrap invisible: the visible window always sits in the middle copy.
  const reel = (value, i) => {
    const strip = stripsNow()[i], at = cellFor(i, value), swap = swapped[i];
    return `<div class="slots-reel" role="img" aria-label="${esc(data.symbols[value].name)}"><div class="slots-strip" aria-hidden="true" style="transform:translate3d(0,${70 - (strip.length + at) * 140}px,0)">${[...strip, ...strip, ...strip].map((s, c) => `<span class="slots-symbol">${glyph(swap && c % strip.length === swap.cell ? swap.value : s)}</span>`).join('')}</div></div>`;
  };

  // Win fanfares grow with the tier: a short arpeggio, then longer runs with a lower voice under them.
  const WIN_NOTES = [
    [523.25, 659.25, 783.99, 1046.5],
    [523.25, 659.25, 783.99, 1046.5, 1318.51, 1567.98],
    [523.25, 659.25, 783.99, 1046.5, 783.99, 1046.5, 1318.51, 1567.98, 2093],
    [392, 523.25, 659.25, 783.99, 659.25, 783.99, 1046.5, 1318.51, 1046.5, 1318.51, 1567.98, 2093],
    [261.63, 329.63, 392, 523.25, 392, 523.25, 659.25, 783.99, 659.25, 783.99, 1046.5, 1318.51, 1046.5, 1318.51, 1567.98, 2093, 1567.98, 2093, 2637, 3135.96],
  ];
  // Every sound is synthesized. kind: start (the lever and a whoosh), stop (a reel's thunk; index = reel), tick (a
  // teased reel creeping past a symbol; index = tease level), beat (a heartbeat while it teeters), snap (its final
  // snap), teaseWin (a crash and a rising sting), teaseLose (a sad trombone), win (the fanfare; index = tier).
  let noiseBuf = null;
  function sound(kind, index = 0) {
    if (muted || document.hidden || state.view !== 'slots') return;
    try {
      audio ||= new (window.AudioContext || window.webkitAudioContext)();
      audio.resume().catch(() => {});
      const now = audio.currentTime;
      // One voice: an oscillator (optionally gliding to `glide` Hz, optionally through a low-pass at `lp` Hz) with a
      // quick attack and an exponential fade over `length` seconds.
      const tone = (frequency, at, type, level, length, { glide, lp, vibrato } = {}) => {
        const o = audio.createOscillator(), g = audio.createGain();
        o.type = type; o.frequency.setValueAtTime(frequency, at);
        if (glide) o.frequency.exponentialRampToValueAtTime(glide, at + length);
        let head = o;
        if (lp) { const f = audio.createBiquadFilter(); f.type = 'lowpass'; f.frequency.value = lp; o.connect(f); head = f; }
        if (vibrato) {
          const l = audio.createOscillator(), lg = audio.createGain();
          l.frequency.value = 6; lg.gain.value = vibrato; l.connect(lg).connect(o.frequency); l.start(at); l.stop(at + length + .05);
        }
        g.gain.setValueAtTime(0, at); g.gain.linearRampToValueAtTime(level, at + .01);
        g.gain.exponentialRampToValueAtTime(.001, at + length);
        head.connect(g).connect(audio.destination); o.start(at); o.stop(at + length + .05);
        o.onended = () => { o.disconnect(); g.disconnect(); };
      };
      // Filtered white noise: whooshes, clicks and cymbals.
      const noise = (at, length, level, type, freq, sweepTo) => {
        if (!noiseBuf) {
          noiseBuf = audio.createBuffer(1, audio.sampleRate * 2, audio.sampleRate);
          const d = noiseBuf.getChannelData(0);
          for (let k = 0; k < d.length; k++) d[k] = Math.random() * 2 - 1;
        }
        const src = audio.createBufferSource(), f = audio.createBiquadFilter(), g = audio.createGain();
        src.buffer = noiseBuf; f.type = type; f.frequency.setValueAtTime(freq, at);
        if (sweepTo) f.frequency.exponentialRampToValueAtTime(sweepTo, at + length);
        g.gain.setValueAtTime(level, at); g.gain.exponentialRampToValueAtTime(.001, at + length);
        src.connect(f).connect(g).connect(audio.destination); src.start(at); src.stop(at + length + .05);
        src.onended = () => { src.disconnect(); f.disconnect(); g.disconnect(); };
      };
      if (kind === 'start') {
        tone(160, now, 'square', .05, .12, { glide: 70, lp: 900 });
        noise(now + .05, .5, .09, 'bandpass', 400, 3000);
      } else if (kind === 'stop') {
        tone(150 - index * 18, now, 'sine', .22, .2, { glide: 55 });
        noise(now, .05, .07, 'highpass', 2500);
      } else if (kind === 'clank') {
        // A metal ratchet: a short bright click with a little body under it, a touch different per reel.
        noise(now, .026, .04, 'bandpass', 1500 + index * 450 + Math.random() * 300);
        tone(190 + index * 35, now, 'square', .009, .03, { lp: 1200 });
      } else if (kind === 'tick') {
        tone(900 + index * 120, now, 'square', .03, .05);
      } else if (kind === 'beat') {
        tone(75, now, 'sine', .32, .16, { glide: 42 });
        tone(70, now + .17, 'sine', .22, .14, { glide: 40 });
      } else if (kind === 'snap') {
        tone(110, now, 'sine', .3, .25, { glide: 45 });
        noise(now, .12, .14, 'lowpass', 2200);
      } else if (kind === 'teaseWin') {
        noise(now, 1.6, .12, 'highpass', 5000);
        [523.25, 659.25, 783.99, 1046.5].forEach((f, k) => tone(f, now + k * .06, 'sawtooth', .045, .9, { lp: 3200 }));
        tone(130.81, now, 'triangle', .12, 1.1);
      } else if (kind === 'teaseLose') {
        // Wah, wah, wah, wahhh.
        [[311.13, 0, .34], [293.66, .38, .34], [277.18, .76, .34], [261.63, 1.14, 1.1]].forEach(([f, at, len], k) =>
          tone(f, now + at, 'sawtooth', .07, len, { lp: 1100, vibrato: k === 3 ? 7 : 0, glide: k === 3 ? 240 : 0 }));
      } else if (kind === 'win') {
        const notes = WIN_NOTES[index], step = index >= 3 ? .08 : .09;
        notes.forEach((f, k) => {
          const at = now + k * step;
          tone(f, at, index >= 3 ? 'triangle' : 'sine', .06, .2);
          if (index >= 2 && k % 4 === 0) tone(f / 2, at, 'sine', .05, .36);
        });
        if (index >= 2) noise(now, .8 + index * .4, .05 + index * .02, 'highpass', 6000);
        if (index >= 3) tone(65.41, now, 'triangle', .14, 1.2 + index * .3);
      }
    } catch (_) { /* sound is optional */ }
  }

  // A rising drone under a tease, for `seconds`; returns a function that cuts it off when the reel stops.
  function drone(seconds, level) {
    if (muted || document.hidden || state.view !== 'slots') return () => {};
    try {
      audio ||= new (window.AudioContext || window.webkitAudioContext)();
      const at = audio.currentTime, o = audio.createOscillator(), g = audio.createGain();
      o.type = 'sawtooth'; o.frequency.setValueAtTime(110, at); o.frequency.exponentialRampToValueAtTime(220 + level * 70, at + seconds);
      g.gain.setValueAtTime(0, at); g.gain.linearRampToValueAtTime(.018, at + .3);
      o.connect(g).connect(audio.destination); o.start(at); o.stop(at + seconds + .5);
      o.onended = () => { o.disconnect(); g.disconnect(); };
      return () => { try { g.gain.cancelScheduledValues(audio.currentTime); g.gain.setTargetAtTime(0, audio.currentTime, .05); } catch (_) { /* ended */ } };
    } catch (_) { return () => {}; }
  }

  // A full-screen layer for falling things, flashes and the big-win banner; it removes itself.
  function fxLayer(ms) {
    const layer = document.createElement('div');
    layer.className = 'slots-fx-layer';
    document.body.appendChild(layer);
    setTimeout(() => layer.remove(), ms);
    return layer;
  }
  function rain(layer, make, n, spread) {
    const w = window.innerWidth, h = window.innerHeight;
    for (let i = 0; i < n; i++) {
      const el = make(i);
      el.classList.add('slots-fx-drop');
      layer.appendChild(el);
      const x = Math.random() * w, drift = (Math.random() - .5) * 240, spin = (Math.random() - .5) * 900;
      const duration = 1600 + Math.random() * 1400;
      el.animate([
        { transform: `translate(${x}px, -160px) rotate(0deg)` },
        { transform: `translate(${x + drift}px, ${h + 160}px) rotate(${spin}deg)` },
      ], { duration, delay: Math.random() * spread, easing: 'cubic-bezier(.35, 0, .75, 1)', fill: 'both' });
    }
  }
  const image = (src, cls) => () => { const img = document.createElement('img'); img.src = src; img.alt = ''; img.className = cls; return img; };
  const emoji = (chars) => (i) => { const s = document.createElement('span'); s.textContent = chars[i % chars.length]; s.className = 'slots-fx-emoji'; return s; };
  function flash(layer, times) {
    const el = document.createElement('div');
    el.className = 'slots-fx-flash';
    layer.appendChild(el);
    el.animate(Array.from({ length: times }, () => [{ opacity: 0 }, { opacity: .32 }]).flat().concat({ opacity: 0 }),
      { duration: times * 480, easing: 'ease-out' });
  }
  function banner(layer, img, title, line, ms, cls = '') {
    const el = document.createElement('div');
    el.className = `slots-fx-banner ${cls}`;
    el.innerHTML = `<img src="${esc(img)}" alt=""><b>${esc(title)}</b><span>${esc(line)}</span>`;
    layer.appendChild(el);
    const frames = reduced()
      ? [{ opacity: 0 }, { opacity: 1, offset: .1 }, { opacity: 1, offset: .85 }, { opacity: 0 }]
      : [{ opacity: 0, transform: 'translate(-50%, -50%) scale(.2) rotate(-8deg)' },
        { opacity: 1, transform: 'translate(-50%, -50%) scale(1.12) rotate(3deg)', offset: .12 },
        { opacity: 1, transform: 'translate(-50%, -50%) scale(1) rotate(0deg)', offset: .2 },
        { opacity: 1, transform: 'translate(-50%, -50%) scale(1.04)', offset: .85 },
        { opacity: 0, transform: 'translate(-50%, -50%) scale(1.3)' }];
    el.animate(frames, { duration: ms, easing: 'ease-out', fill: 'both' });
  }
  function shake(el, px, ms) {
    const n = Math.round(ms / 50);
    const frames = Array.from({ length: n }, (_, i) => {
      const k = px * (1 - i / n);
      return { transform: `translate(${(Math.random() - .5) * 2 * k}px, ${(Math.random() - .5) * 2 * k}px) rotate(${(Math.random() - .5) * k / 6}deg)` };
    });
    el.animate([...frames, { transform: 'none' }], { duration: ms, easing: 'linear' });
  }
  // Diamond < banana < Onkey < Golden Onkey: each tier adds to the one below it. Reduced motion keeps the glow, the
  // sound and (for both Onkeys) the banner, without movement or flashes.
  function celebrate(spin) {
    const tier = tierOf(spin);
    if (spin.net > 0) sound('win', tier);
    if (!tier) return;
    const glass = $('.slots-glass'), cabinet = $('.slots-cabinet'), calm = reduced();
    const payout = `${money(spin.payout)} credits`;
    if (tier === 1) { confetti?.(glass, false, { emoji: ['💎', '✨'] }); return; }
    if (tier === 2) {
      confetti?.(glass, true, { emoji: ['🍌'] });
      if (!calm) { shake(cabinet, 5, 450); rain(fxLayer(4000), emoji(['🍌']), 36, 900); }
      return;
    }
    if (tier === 3) {
      const layer = fxLayer(5000);
      banner(layer, '/assets/onkey.png', 'ONKEY!', payout, 3200);
      if (calm) return;
      flash(layer, 1); shake(cabinet, 10, 900);
      confetti?.(glass, true); setTimeout(() => confetti?.(glass, true), 500);
      rain(layer, image('/assets/onkey-logo.png', 'slots-fx-onkey'), 28, 1400);
      return;
    }
    const layer = fxLayer(8000);
    layer.classList.add('dim');
    banner(layer, '/assets/onkey-logo.png', 'GOLDEN ONKEY!!!', payout, 5600, 'golden');
    if (calm) return;
    flash(layer, 3); shake(cabinet, 16, 1800);
    [0, 450, 900, 1500, 2200].forEach((ms) => setTimeout(() => confetti?.(glass, true), ms));
    rain(layer, image('/assets/onkey-logo.png', 'slots-fx-onkey golden'), 90, 3600);
  }

  // Cubic Hermite: from `from`, cover `dist` in `dur` seconds, starting at velocity m0 / dur and ending at m1 / dur.
  const hermite = (from, dist, m0, m1) => (t) => from + m0 * (t * t * t - 2 * t * t + t) + dist * (-2 * t * t * t + 3 * t * t) + m1 * (t * t * t - t * t);
  // A snap with a little overshoot before it settles.
  const backOut = (t) => 1 + 2.70158 * (t - 1) ** 3 + 1.70158 * (t - 1) ** 2;
  function startMotion() {
    const start = performance.now(), positions = reelsNow().map((value, i) => cellFor(i, value)), brakes = [], stopped = [false, false, false];
    const origin = [...positions];
    let previous = start, frame = 0, targets = null, dues = null, ended = false, resolve, tease = null;
    const clanked = [0, 0, 0];
    let lastClank = 0;
    const done = new Promise((r) => { resolve = r; });
    const finish = () => { ended = true; cancelAnimationFrame(frame); tease?.quiet?.(); resolve(); };
    function paint() {
      $$('.slots-strip').forEach((strip, i) => {
        strip.style.transform = `translate3d(0,${70 - (cells() + wrap(positions[i])) * 140}px,0)`;
        strip.parentElement.classList.toggle('stopped', stopped[i]);
        strip.parentElement.classList.toggle('teasing', !!tease?.on && i === 2 && !stopped[i]);
        if (stopped[i]) strip.parentElement.setAttribute('aria-label', data.symbols[targets[i]].name);
      });
      $('.slots-glass')?.classList.toggle('teasing', !!tease?.on && !stopped[2]);
      $('.slots-glass')?.classList.toggle('teetering', !!tease?.teeter);
    }
    // Where a reel stops: the first cell holding the result after `run` cells; a secret symbol has no cell, so it
    // takes over whichever cell that is. `prefer` narrows the cells first (a tease's near miss) when any qualify.
    function landing(i, from, run, prefer) {
      const strip = stripsNow()[i], secret = !strip.includes(targets[i]);
      let options = strip.flatMap((sym, c) => (secret || sym === targets[i] ? [c] : []));
      const near = prefer ? options.filter(prefer) : [];
      if (near.length) options = near;
      const cell = options.reduce((best, c) => (wrap(c - from - run) < wrap(best - from - run) ? c : best));
      return { cell, secret, dist: run + wrap(cell - from - run) };
    }
    function brake(i, now, from, velocity) {
      if (tease && i === 2) {
        // Keep going flat out, slow to a crawl, then creep (longer and slower for bigger pairs) to stop half on the
        // matching symbol and half on its neighbour, teeter there to a heartbeat, and snap: forward or back onto the
        // match on a win; on a loss, back when the match was just short (the cell below) or forward off it when it
        // had crept just past (the cell above). Lining up the landing cell only adds full-speed spin.
        const lvl = tease.level, hold = .5 + lvl * .2, crawl = 2.6 - lvl * .25, creep = 1.5 + lvl * .15;
        const slowDur = 1 + lvl * .1, slowDist = (velocity + crawl) / 2 * slowDur, creepDur = 2 * creep / crawl;
        const teeterDur = 1.1 + lvl * .15, beats = lvl >= 3 ? 4 : 3, snapDur = .34;
        const pair = targets[0], win = targets[2] === pair, strip = stripsNow()[i], n = strip.length;
        const below = (c) => strip[(c + 1) % n] === pair, above = (c) => strip[(c + n - 1) % n] === pair;
        const { cell, secret, dist } = landing(i, from, velocity * hold + slowDist + creep, win ? null : (c) => below(c) || above(c));
        const back = win ? Math.random() < .5 : below(cell) && (!above(cell) || Math.random() < .5);
        const off = back ? .5 : -.5;  // where it teeters, from the landing cell
        const holdDist = dist + off - slowDist - creep, holdDur = holdDist / velocity, edge = from + dist + off;
        tease.on = true; tease.won = win;
        tease.quiet = drone(holdDur + slowDur + creepDur + teeterDur, lvl);
        const segs = [
          { dur: holdDur, at: hermite(from, holdDist, velocity * holdDur, velocity * holdDur) },
          { dur: slowDur, at: hermite(from + holdDist, slowDist, velocity * slowDur, crawl * slowDur) },
          { dur: creepDur, at: hermite(from + holdDist + slowDist, creep, crawl * creepDur, 0), ticks: true },
          // Rocking a little either way, a bit more each beat, and back to the edge at the end.
          { dur: teeterDur, at: (u) => edge + .09 * Math.sin(2 * Math.PI * beats * u) * (.5 + .5 * u), beats, teeter: true },
          { dur: snapDur, at: (u) => edge - off * backOut(u), snap: true },
        ];
        return { at: now, segs, end: from + dist, cell, secret };
      }
      const dur = (1100 + i * 160) / 1000, run = velocity * dur / 2;
      const { cell, secret, dist } = landing(i, from, run);
      return { at: now, segs: [{ dur, at: hermite(from, dist, velocity * dur, 0) }], end: from + dist, cell, secret };
    }
    function tick(now) {
      if (ended) return;
      if (state.view !== 'slots' || reduced()) { finish(); return; }
      const dt = Math.min((now - previous) / 1000, .05); previous = now;
      positions.forEach((position, i) => {
        if (stopped[i]) return;
        const velocity = (14 + i) * Math.min((now - start) / 420, 1);
        // A swapped-in secret symbol goes back to its strip symbol once it has rolled out of view (1.5 cells shows).
        if (swapped[i] && !brakes[i]?.secret && position - origin[i] > 2.5) {
          setCell(i, swapped[i].cell, stripsNow()[i][swapped[i].cell]);
          swapped[i] = null;
        }
        if (targets && now >= dues[i] && !brakes[i]) brakes[i] = brake(i, now, position + velocity * dt, velocity);
        const b = brakes[i];
        if (!b) {
          positions[i] += velocity * dt;
          if (Math.floor(positions[i] + .5) !== Math.floor(position + .5)) clank(i, now);
          return;
        }
        let t = (now - b.at) / 1000, k = 0;
        while (k < b.segs.length - 1 && t > b.segs[k].dur) { t -= b.segs[k].dur; k++; }
        const sg = b.segs[k], u = Math.min(t / sg.dur, 1);
        positions[i] = sg.at(u);
        if (Math.floor(positions[i] + .5) !== Math.floor(position + .5)) {
          if (sg.ticks) sound('tick', tease.level);
          else if (!sg.teeter && !sg.snap) clank(i, now);
        }
        if (sg.teeter) {
          tease.teeter = true;
          const beat = Math.floor(u * sg.beats);
          if (beat !== b.beat && beat < sg.beats) { b.beat = beat; sound('beat'); }
        }
        if (sg.snap && !b.snapped) {
          b.snapped = true; tease.teeter = false; sound('snap');
          const cabinet = $('.slots-cabinet');
          if (cabinet) shake(cabinet, 5, 260);
        }
        // Swap the secret symbol in while its cell is still out of view below the window.
        if (b.secret && !swapped[i] && b.end - positions[i] < 2.5) {
          swapped[i] = { cell: b.cell, value: targets[i] };
          setCell(i, b.cell, targets[i]);
        }
        if (k === b.segs.length - 1 && u === 1) {
          positions[i] = b.end;
          stopped[i] = true; rest[i] = b.cell; sound('stop', i);
          if (tease && i === 2) { tease.quiet?.(); sound(tease.won ? 'teaseWin' : 'teaseLose'); }
        }
      });
      paint();
      if (stopped.every(Boolean)) finish();
      else frame = requestAnimationFrame(tick);
    }
    // The ratchet of a symbol passing the line: one stream for the machine, at most every 70 ms across the reels.
    function clank(i, now) {
      if (now - clanked[i] < 35 || now - lastClank < 70) return;
      clanked[i] = lastClank = now; sound('clank', i);
    }
    if (!reduced()) frame = requestAnimationFrame(tick);
    else finish();
    return {
      paint, cancel: finish,
      // Whether the last spin teased, and if so whether it won (for the flash after the redraw).
      teased: () => (tease?.on ? { won: tease.won } : null),
      settle: (reels) => {
        targets = reels;
        const stopAt = Math.max(performance.now(), start + 800), keyOf = (r) => data.symbols[r]?.key;
        // Decided from the first two reels only.
        const key = reels[0] === reels[1] ? keyOf(reels[0]) : null;
        if (key && Math.random() < (TEASE[key] || 0)) tease = { level: TEASE_LEVEL[key] || 0, on: false };
        // When each reel starts braking (they take 1.1, 1.26 and 1.42 s): 180 ms apart plus the pauses; a teased
        // third reel starts its run-in once the second has stopped.
        const bigWin = reels.every((r) => r === reels[0]) && BIG_OPENING.includes(keyOf(reels[0]));
        const opening = bigWin || Math.random() < .1 ? 700 + Math.random() * 500 : 0;
        const gap1 = PAUSE[keyOf(reels[0])] || 0;
        const gap2 = reels[0] === reels[1] ? (PAUSE[keyOf(reels[1])] || 0) * 1.5 + 150 : (PAUSE[keyOf(reels[1])] || 0) * .5;
        const first = stopAt + opening, second = first + 180 + gap1;
        dues = [first, second, tease ? second + 1260 + 150 : second + 180 + gap2];
        return done;
      },
    };
  }

  const timeOf = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  function history() {
    return `<section class="card slots-history"><h2>Your recent spins</h2>
      ${data.history.length ? `<div class="table-wrap"><table><thead><tr><th>Reels</th><th>Time</th><th class="num">Stake</th><th class="num">Returned</th><th class="num">Net</th></tr></thead><tbody>${data.history.map((s) => `<tr><th scope="row" class="slots-history-reels">${s.reels.map(icon).join('')}</th><td class="muted">${esc(timeOf(s.created_ts))}</td><td class="num">${money(s.stake)}</td><td class="num">${money(s.payout)}</td><td class="num ${s.net > 0 ? 'up' : s.net < 0 ? 'down' : ''}">${signed(s.net)}</td></tr>`).join('')}</tbody></table></div>` : `<p class="muted">${state.me ? 'Your first spin will appear here.' : 'Sign in to play and see your spin history.'}</p>`}</section>`;
  }
  // This season at a glance: spins and net, how often you win against the odds, your biggest win, and the dry spell.
  function season(m) {
    const me = data.me;
    if (!me) return '<section class="card slots-season"><h2>Your season</h2><p class="muted">Sign in to see your season at the slots.</p></section>';
    const expected = 1 / m.chances.reduce((a, c) => a + c, 0);
    const tile = (label, value, sub = '', cls = '') => `<div class="slots-stat"><span>${label}</span><b class="${cls}">${value}</b>${sub ? `<small>${sub}</small>` : ''}</div>`;
    return `<section class="card slots-season"><h2>Your season</h2><div class="slots-stats">
      ${tile('Spins', fmt.n0(me.spins), `${money(me.staked)} credits staked`)}
      ${tile('Net', `${signed(me.net)}`, 'credits', me.net > 0 ? 'up' : me.net < 0 ? 'down' : '')}
      ${tile('Wins', fmt.n0(me.wins), me.wins ? `1 in ${(me.spins / me.wins).toFixed(1)} · expected 1 in ${expected.toFixed(1)}` : `expected 1 in ${expected.toFixed(1)}`)}
      ${tile('Since your last win', fmt.n0(me.since_win), me.since_win === 1 ? 'spin' : 'spins')}
      ${tile('Biggest win', me.best ? `${money(me.best.payout)}` : '–', me.best ? `<span class="slots-mini-reels">${me.best.reels.map(icon).join('')}</span> ${money(me.best.stake)} staked` : 'No wins yet')}
    </div></section>`;
  }
  // The squad's biggest wins this season.
  function bigWins() {
    const rows = data.big_wins || [];
    return `<section class="card slots-big"><h2>Biggest wins this season</h2>
      ${rows.length ? `<ol class="slots-big-list">${rows.map((w, k) => `<li><span class="slots-big-rank">${k + 1}</span><span class="slots-big-who">${plainName ? plainName(w.bettor) : esc(w.bettor)}</span><span class="slots-mini-reels" aria-label="${esc(w.reels.map((r) => data.symbols[r].name).join(', '))}">${w.reels.map(glyph).join('')}</span><span class="slots-big-pay"><b>+${money(w.net)}</b><small>${w.multiplier}× on ${money(w.stake)} · ${esc(fmt.date(w.created_ts * 1000))}</small></span></li>`).join('')}</ol>` : '<p class="muted">No wins yet this season.</p>'}
    </section>`;
  }
  // The pay table: every symbol but the secret one, largest payout first, with its chance and how often you've hit it.
  function paytable(m, win) {
    const lines = data.lines;
    const rows = data.symbols.map((s, i) => i).filter((i) => !data.symbols[i].secret).sort((a, b) => m.triples[b] - m.triples[a]);
    const note = !lines ? '' : lines.spins
      ? `Your hits count ${fmt.n0(lines.spins)} spin${lines.spins === 1 ? '' : 's'} since ${esc(fmt.date(lines.since * 1000))}.`
      : 'Your hits are counted from your next spin.';
    return `<aside class="card slots-paytable"><h2>The payouts</h2><p class="muted small">Match three on the centre line.<br>Payouts include your stake.</p>
      <div class="slots-prizes">${rows.map((i) => `<div class="slots-prize ${win && result.reels.every((r) => r === i) ? 'won' : ''}"><span class="slots-pay-left"><span class="slots-pay-symbol" aria-hidden="true">${glyph(i).repeat(3)}</span><span class="sr-only">Three ${esc(data.symbols[i].name)}</span><small>1 in ${fmt.n0(Math.round(1 / m.chances[i]))}${lines ? ` · <span class="slots-hits">you've hit ${fmt.n0(lines.hits[i])}</span>` : ''}</small></span><span><b>${m.triples[i]}×</b><small>${money(m.triples[i] * stake)} credits</small></span></div>`).join('')}</div>
      ${note ? `<p class="muted small slots-lines-note">${note}</p>` : ''}
      <details class="how"><summary>Every spin is independent.</summary><p>Each reel stops on a symbol at random; the chance beside each line is for all three reels matching it. Pairs and mixed symbols pay 0. Average return: ${m.rtp}% over many spins. Results settle immediately and cannot be cancelled.</p><p>Slot results have their own season totals. Match-betting ROI stays separate. Slots do not earn shop bananas.</p></details>
    </aside>`;
  }
  // The pull lever on the cabinet's side: another way to spin, which swings down when you do.
  function lever(insufficient) {
    const parts = '<span class="slots-lever-plate"><i></i><i></i><span class="slots-lever-slot"></span></span><span class="slots-lever-rod"></span><span class="slots-lever-hub"></span><span class="slots-lever-knob"></span>';
    if (!state.me) return `<button class="slots-lever" data-signin aria-label="Sign in to spin">${parts}</button>`;
    return `<button class="slots-lever" id="slot-lever" aria-label="Pull the lever to spin" ${busy || (insufficient && !pending) ? 'disabled' : ''}>${parts}</button>`;
  }
  // The machine's own readout: your credits, the bet and the last win, in lit digits.
  function readout() {
    const cell = (label, value) => `<div><span>${label}</span><b>${value}</b></div>`;
    return `<div class="slots-well slots-readout">${cell('Credits', state.me ? money(state.me.balance) : '–')}${cell('Bet', money(stake))}${cell('Win', result && !busy ? money(result.payout) : '0')}</div>`;
  }
  function view() {
    syncOwner();
    if (!data || (data.me?.name || null) !== owner) {
      load().then(() => { if (state.view === 'slots') draw(); }).catch(() => {
        const el = $('#slot-loading'); if (el) el.textContent = 'Could not load slots. Open this tab again to retry.';
      });
      return '<section class="card" id="slot-loading">Loading slots…</section>';
    }
    const m = data.machines.find((g) => g.key === 'jackpot'), locked = busy || !!pending;
    const insufficient = state.me && state.me.balance < stake;
    const win = !busy && result?.net > 0, tier = busy ? 0 : tierOf(result);
    const message = result ? result.payout ? `${money(result.payout)} credits returned` : 'No winning line' : 'Ready when you are';
    const detail = result ? `${signed(result.net)} credits net` : 'Three matching symbols on the centre line pays.';
    return `<div class="slots-layout"><section class="slots-cabinet ${win ? 'slots-win' : ''} ${tier ? `slots-tier-${tier}` : ''} ${busy ? 'slots-busy' : ''}"><div class="slots-leds" aria-hidden="true">${LEDS}</div><div class="slots-body">
        <div class="slots-marquee"><h2>Slots</h2><button class="slots-sound" id="slot-sound" aria-pressed="${!muted}" aria-label="${soundLabel()}" title="${soundLabel()}">${speaker(muted)}</button></div>
        <div class="slots-stage"><div class="slots-window"><div class="slots-glass"><div class="slots-reels ${busy ? 'spinning' : ''}" aria-label="${busy ? 'Reels spinning' : 'Reel result'}" aria-busy="${busy}">${reelsNow().map(reel).join('')}</div><div class="slots-payline" aria-hidden="true"></div><span class="slots-line-arrow left" aria-hidden="true">▸</span><span class="slots-line-arrow right" aria-hidden="true">◂</span></div>${lever(insufficient)}
</div>
          <div class="slots-result" role="status" aria-live="polite"><b>${busy ? 'Spinning…' : esc(message)}</b><span>${busy ? 'Let them roll.' : esc(detail)}</span></div>
        </div>
        <div class="slots-deck"><span class="slots-screw" aria-hidden="true"></span><span class="slots-screw" aria-hidden="true"></span><span class="slots-screw" aria-hidden="true"></span><span class="slots-screw" aria-hidden="true"></span>
          <div class="slots-well slots-stakes" role="group" aria-label="Credits per spin">${data.stakes.map((s) => `<button type="button" data-slot-stake="${s}" aria-pressed="${s === stake}" aria-label="Bet ${s} credits" ${locked ? 'disabled' : ''}><small>Bet</small><b>${s}</b></button>`).join('')}</div>
          ${readout()}
          <div class="slots-well slots-spin-well"><div class="slots-spin-ring">${state.me ? `<button class="slots-spin" id="slot-spin" ${busy || (insufficient && !pending) ? 'disabled' : ''} aria-label="${busy ? 'Spinning' : pending ? 'Check last spin' : `Spin for ${stake} credits`}"><span>${busy ? '···' : pending ? 'Check' : 'Spin'}</span>${pending && !busy ? '<small>last spin</small>' : ''}</button>` : '<button class="slots-spin" data-signin><span>Sign in</span><small>to spin</small></button>'}</div></div>
        </div>
        <p class="slots-error" role="alert">${esc(error || (insufficient && !pending ? 'Not enough credits. Choose a smaller stake.' : ''))}</p>
      </div></section>${paytable(m, win)}</div>
      <div class="slots-lower">${history()}${season(m)}${bigWins()}</div>`;
  }
  async function spin() {
    if (busy || !state.me) return;
    if (!pending) {
      const bytes = new Uint8Array(16); crypto.getRandomValues(bytes);
      remember({ stake, request_id: Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('') });
    }
    const name = owner, request = pending;
    let landed = null;
    busy = true; error = ''; sound('start'); draw();
    $('.slots-lever')?.classList.add('pulled');
    const rolling = motion = startMotion();
    try {
      const out = await api('/api/slots/spin', { method: 'POST', body: JSON.stringify(request) });
      await rolling.settle(out.spin.reels);
      if (owner !== name) return;
      result = landed = out.spin; remember(null);
      if (state.me?.name === name) state.me.balance = out.balance;
      await Promise.all([load(), loadMe()]);
    } catch (e) {
      if (owner === name) {
        if (e.status >= 400 && e.status < 500) remember(null);
        error = pending ? 'The result could not be confirmed. Check last spin to recover it without paying twice.' : e.message;
      }
    } finally {
      const teased = rolling.teased();
      rolling.cancel(); motion = null; busy = false;
      if (state.view === 'slots') {
        draw(); $('#slot-spin')?.focus();
        if (landed && teased) $$('.slots-reel')[2]?.classList.add(teased.won ? 'tease-won' : 'tease-lost');
        if (landed) celebrate(landed);
      }
    }
  }
  function bind(viewEl) {
    $$('[data-slot-stake]', viewEl).forEach((button) => button.addEventListener('click', () => {
      stake = Number(button.dataset.slotStake); error = ''; draw();
      $(`[data-slot-stake="${stake}"]`)?.focus();
    }));
    $('#slot-spin', viewEl)?.addEventListener('click', spin);
    $('#slot-lever', viewEl)?.addEventListener('click', spin);
    $('#slot-sound', viewEl)?.addEventListener('click', (e) => {
      muted = !muted; localStorage.setItem('fs.slotsMuted', muted ? '1' : '0');
      const b = e.currentTarget;
      b.innerHTML = speaker(muted); b.setAttribute('aria-pressed', String(!muted)); b.setAttribute('aria-label', soundLabel()); b.title = soundLabel();
    });
    if (state.view === 'slots') motion?.paint();
  }
  return { init, load, view, bind };
})();
