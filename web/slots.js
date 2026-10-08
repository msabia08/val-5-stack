/* Server-settled slots. Reel motion is cosmetic; only the server chooses the result. */
window.FiveSlots = (() => {
  'use strict';
  let state, $, $$, api, draw, esc, fmt, loadMe, confetti, plainName, holdBalance, releaseBalance, displayBalance;
  let data = null, owner = null, stake = 10, busy = false, result = null, error = '', pending = null;
  let motion = null, audio = null, muted = localStorage.getItem('fs.slotsMuted') === '1';
  // Wins on these symbols get bigger celebrations, 1 to 4 (celebrate()); other wins just light the cabinet.
  const FX = { diamond: 1, banana: 2, monkey: 3, golden: 4 };
  // The tease: when the first two reels match, the third may keep spinning, glow, then creep up to the payline so you
  // can't tell whether it will land. The chance depends only on the pair (never on whether the third reel wins, so a
  // tease gives nothing away) and grows with what the pair could pay; cherry, bell and spike pairs never tease.
  // TEASE_LEVEL (0-4) sets how long and slow it is.
  const TEASE = { cherry: 0, bell: 0, spike: 0, diamond: .55, banana: .75, monkey: .9, golden: 1 };
  const TEASE_LEVEL = { cherry: 0, bell: 0, spike: 1, diamond: 2, banana: 3, monkey: 4, golden: 4 };
  // Stops slow down for bigger symbols: the wait (ms) before the next reel grows with the symbol the last one showed,
  // longer still after a matching pair, so they follow what's on screen and come on losses too. A long opening spin
  // (BIG_OPENING) comes with every banana, Onkey or Golden Onkey win and one spin in ten besides, so it's no giveaway.
  const PAUSE = { cherry: 0, bell: 60, spike: 150, diamond: 300, banana: 450, monkey: 650, golden: 900 };
  const BIG_OPENING = ['banana', 'monkey', 'golden'];
  // Auto: the Auto key beside the Spin button is on or off. On, it spins again after every spin until it's switched
  // off, the credits run short, a spin fails or you leave the tab. The pause before the next spin is longer when
  // there's a win to read, and longer still for one with a celebration.
  const AUTO_GAP_MS = 500, AUTO_WIN_GAP_MS = 1300, AUTO_BIG_GAP_MS = 4500;
  let auto = false;
  // Auto's settings, in the gear menu at the marquee's left (settingsMenu()), both remembered: whether Auto takes a
  // hold on offer (it does unless switched off; each costs the stake again), after AUTO_HOLD_GAP_MS so the offer is
  // seen, and how many spins a run lasts (autoCap, one of AUTO_CAPS; 0 is no limit). autoLeft counts a capped run
  // down; a hold isn't one of its spins.
  const AUTO_CAPS = [0, 10, 25, 50, 100], AUTO_HOLD_GAP_MS = 900;
  let autoHold = localStorage.getItem('fs.slotsAutoHold') !== '0';
  let autoCap = AUTO_CAPS.includes(Number(localStorage.getItem('fs.slotsAutoCap'))) ? Number(localStorage.getItem('fs.slotsAutoCap')) : 0;
  let autoLeft = 0, settingsOpen = false;
  // Turbo: the Turbo key beside Auto shortens everything about a spin (the run-up, each reel's stop, the pauses between
  // them, Auto's pause) to TURBO of its length. The tease is never shortened: it's the one part worth watching.
  const TURBO = .4;
  let turbo = localStorage.getItem('fs.slotsTurbo') === '1';
  const quick = (ms) => (turbo ? ms * TURBO : ms);
  // Hold: a losing spin with a pair is sometimes offered one by the server (spin.hold: the reel that spins again, the
  // pair's symbol, its line's multiplier and the chance). The Hold button under the reel window takes it for the
  // spin's stake: the pair stays and the third reel spins alone. Any other spin lets the offer go.
  const holdOffer = () => (!busy && !pending && state.me && result?.hold) || null;
  // Nudge: NUDGE_CHANCE of wins (never a teased one) stop the deciding reel one symbol short, looking lost, then bump
  // it onto the line NUDGE_WAIT seconds later. It's only how a win arrives: the server's result is never changed.
  const NUDGE_CHANCE = .25, NUDGE_WAIT = .6;
  // Onkey's walk-in: when a spin's result shows Onkey's own symbol, CAMEO_CHANCE of the time (GOLDEN_CAMEO_CHANCE when
  // it shows a Golden Onkey), instead of a tease or a nudge, that reel stops on an empty cell and Onkey walks down from
  // the logo to sit in it (FiveOnkey.visit()), becoming the symbol; for the Golden Onkey he turns gold on the way.
  // He walks back when the next spin starts. The result is the server's all along: blank[i] is the cell drawn empty
  // until he's there, cameoAt where he's sitting (reel, cell, golden).
  const CAMEO_CHANCE = .12, GOLDEN_CAMEO_CHANCE = .5, CAMEO_MAX_MS = 2800;
  let blank = [null, null, null], cameoAt = null;
  // Where the symbol in a reel's cell is drawn, as a viewport rect: its picture's own box, so Onkey ends his walk
  // exactly on it. Without a picture there, the middle of the reel's window.
  function reelSpot(i, cell) {
    const img = $$('.slots-strip')[i]?.children[cells() + cell]?.querySelector('img')?.getBoundingClientRect();
    if (img?.width) return { left: img.left, top: img.top, width: img.width, height: img.height };
    const r = $$('.slots-reel')[i]?.getBoundingClientRect();
    if (!r || !r.width) return null;
    const side = Math.min(r.width, r.height / 2) * .62;
    return { left: r.left + (r.width - side) / 2, top: r.top + (r.height - side) / 2, width: side, height: side };
  }
  // Daily spins (data.daily, from the server): while any are left today, a spin is free, at the daily stake.
  const freeLeft = () => (state.me && data?.daily?.left) || 0;
  const betNow = () => (pending ? pending.stake : freeLeft() ? data.daily.stake : stake);
  // The Golden Onkey (the secret symbol) is wild: it matches anything, and it pays when spotted (slots.py's payouts()).
  const wildIndex = () => data.symbols.findIndex((s) => s.secret);
  const matches = (a, b) => a === b || a === wildIndex() || b === wildIndex();
  // The symbol a matching pair stands for: the other one when one of them is the Golden Onkey.
  const pairSymbol = (a, b) => (a === wildIndex() ? b : a);
  // The line a spin's reels make (the symbols that aren't Golden Onkeys all match), or -1.
  function lineOf(reels) {
    const rest = reels.filter((r) => r !== wildIndex());
    return !rest.length ? wildIndex() : rest.every((r) => r === rest[0]) ? rest[0] : -1;
  }
  // What a spin paid, part by part (the server's "parts"; a line, then the Golden Onkey spotted).
  const partsOf = (spin) => spin?.parts || [];
  // The order the reels stop in: a matching pair first when the result has one (either of the two first; a real pair
  // before one the Golden Onkey makes), so the last reel to stop is always the one that decides the line; with no pair
  // at all, any order. It only changes which reel stops when, never what any reel shows.
  function stopOrder(reels) {
    const pairs = [[0, 1], [0, 2], [1, 2]], pick = (list) => list[Math.floor(Math.random() * list.length)];
    const same = pairs.filter(([a, b]) => reels[a] === reels[b]), wild = pairs.filter(([a, b]) => matches(reels[a], reels[b]));
    const pair = same.length ? pick(same) : wild.length ? pick(wild) : null;
    const order = pair ? [...pair, 3 - pair[0] - pair[1]] : [0, 1, 2].sort(() => Math.random() - .5);
    if (pair && Math.random() < .5) [order[0], order[1]] = [order[1], order[0]];
    return order;
  }
  const storageKey = (name) => `fs.slotSpin.${name.toLowerCase()}`;
  function init(ctx) {
    ({ state, $, $$, api, draw, esc, fmt, loadMe, confetti, plainName, holdBalance, releaseBalance, displayBalance } = ctx);
    // Space spins (or checks the last spin) on this tab, unless you're typing or on another control.
    document.addEventListener('keydown', (e) => {
      if (state.view !== 'slots' || !['Space', 'KeyH'].includes(e.code) || e.repeat || e.ctrlKey || e.altKey || e.metaKey) return;
      if (e.target.closest?.('input, textarea, select, button, summary, a, [contenteditable], .modal')) return;
      const button = $(e.code === 'KeyH' ? '#slot-hold' : '#slot-spin');  // H takes a hold on offer
      if (!button) return;
      e.preventDefault();
      if (!button.disabled) button.click();
    });
    document.addEventListener('visibilitychange', () => { if (document.hidden && auto) { auto = false; paintAuto(); } });
    // The settings menu closes on a click outside it or Escape.
    document.addEventListener('click', (e) => { if (settingsOpen && !e.target.closest?.('#slot-settings, #slot-settings-menu')) showSettings(false); });
    document.addEventListener('keydown', (e) => { if (settingsOpen && e.key === 'Escape') { showSettings(false); $('#slot-settings')?.focus(); } });
  }
  function syncOwner() {
    const name = state.me?.name || null;
    if (owner === name) return;
    motion?.cancel();
    owner = name; data = null; result = null; error = ''; pending = null; auto = false;
    try { pending = name ? JSON.parse(sessionStorage.getItem(storageKey(name))) : null; } catch (_) { /* unavailable */ }
    if (pending && !pending.daily && pending.hold == null) stake = pending.stake;
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
  // What the reels show: this visit's last result, else your last jackpot spin (so a reload doesn't change the
  // machine), else a starting line-up.
  const reelsNow = () => {
    const last = data.history?.find((s) => s.machine === 'jackpot' && s.reels?.length === 3 && s.reels.every((r) => data.symbols[r]));
    return result?.reels || last?.reels || [0, 3, 5];
  };
  // The LED frame: lights at fixed spots on a rounded outline 8px inside the cabinet's edge, clockwise from the top
  // left: the straight runs end where the corners curve (20px round at the top, 12px at the bottom, following the
  // cabinet's 28px and 20px corners), with a light on each top curve. Each carries its place in a repeating run of 9
  // (--k), which staggers the CSS chase so the light travels round; the lights never move.
  // The phone's cabinet (style.css's phone block) is a third as wide, so its frame has fewer lights along the top and
  // bottom (about 12px apart, like its sides) and more in the corners (two on each top curve, one on each bottom
  // curve), so the spacing stays even all the way round.
  const ledFrame = (phone) => {
    const out = [], TOP = phone ? 28 : 62, SIDE = 40;
    const span = (from, to, n, k) => `calc(${from}px + (100% - ${from + to}px) * ${(k / (n - 1)).toFixed(4)})`;
    const put = (x, y) => out.push(`<i style="left:${x};top:${y};--k:${8 - (out.length % 9)}"></i>`);
    // A light on a corner's curve: `deg` round it clockwise, on a circle of radius r whose centre is c px in from both edges.
    const corner = (right, bottom, c, r, deg) => {
      const t = deg * Math.PI / 180, turn = right === bottom ? [Math.cos(t), Math.sin(t)] : [Math.sin(t), Math.cos(t)];
      const px = (far, v) => (far ? `calc(100% - ${(c - r * v).toFixed(2)}px)` : `${(c - r * v).toFixed(2)}px`);
      put(px(right, turn[0]), px(bottom, turn[1]));
    };
    const topCurve = phone ? [30, 60] : [45], bottomCurve = phone ? [45] : [];
    for (let k = 0; k < TOP; k++) put(span(28, 28, TOP, k), '8px');
    topCurve.forEach((deg) => corner(true, false, 28, 20, deg));
    for (let k = 0; k < SIDE; k++) put('calc(100% - 8px)', span(28, 20, SIDE, k));
    bottomCurve.forEach((deg) => corner(true, true, 20, 12, deg));
    for (let k = 0; k < TOP; k++) put(span(20, 20, TOP, TOP - 1 - k), 'calc(100% - 8px)');
    bottomCurve.forEach((deg) => corner(false, true, 20, 12, deg));
    for (let k = 0; k < SIDE; k++) put('8px', span(28, 20, SIDE, SIDE - 1 - k));
    topCurve.forEach((deg) => corner(false, false, 28, 20, deg));
    return out.join('');
  };
  const LEDS = { wide: ledFrame(false), phone: ledFrame(true) };
  const leds = () => (matchMedia('(max-width: 640px)').matches ? LEDS.phone : LEDS.wide);
  const speaker = (off) => window.speakerIcon(off, 20); // the shared icon (common.js)
  const soundLabel = () => (muted ? 'Turn sound on' : 'Mute sound');
  // Each reel's strip matches the machine's display weights ("show", which losing spins are drawn from), reduced to
  // whole cells (10 cherries, 4 Onkeys in 42), spread evenly, with each reel's spread shifted so the three strips
  // differ. Reel motion is in cells.
  // A secret symbol (the Golden Onkey) isn't on the strips at all: when a reel stops on it, it takes over one cell ("swapped"),
  // put in just before it rolls into view and taken out once it has rolled away on the next spin.
  let strips = null, stripsFor = '', rest = [null, null, null], swapped = [null, null, null];
  // A losing tease teeters between the pair's symbol and the result. When a reel's strip has those two nowhere side by
  // side, the pair's symbol is drawn in the cell next to the landing cell for that spin (decoy[i]: cell, value) and the
  // strip's own symbol comes back once it has rolled out of view on the next. Only the neighbour changes, never the result.
  let decoy = [null, null, null];
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
  // symbol's first cell. A secret symbol with no cell yet (the roll was skipped) takes over the resting cell.
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
    if (strip) [cell, n + cell, 2 * n + cell].forEach((c) => { strip.children[c].innerHTML = value === null ? '' : glyph(value); });  // null: empty
  }
  // A win's celebration tier comes from its line's symbol (a Golden Onkey filling in doesn't change it).
  const lineSymbol = (spin) => partsOf(spin).find((p) => p.kind === 'line')?.symbol;
  const tierOf = (spin) => (spin && spin.net > 0 ? FX[data.symbols[lineSymbol(spin)]?.key] || 0 : 0);
  // The spin that just landed: its Golden Onkeys pop and its cash-outs deal in on the first draw after it, not again.
  let freshId = null;
  // Three copies make the wrap invisible: the visible window always sits in the middle copy. A Golden Onkey resting on
  // the line after a spin glows (.spotted).
  const reel = (value, i) => {
    const strip = stripsNow()[i], at = cellFor(i, value), swap = swapped[i], fake = decoy[i];
    const spotted = !busy && result && value === wildIndex();
    return `<div class="slots-reel${spotted ? ' spotted' : ''}" role="img" aria-label="${esc(data.symbols[value].name)}"><div class="slots-strip" aria-hidden="true" style="transform:translate3d(0,${70 - (strip.length + at) * 140}px,0)">${[...strip, ...strip, ...strip].map((s, c) => `<span class="slots-symbol">${blank[i] === c % strip.length ? '' : glyph(swap && c % strip.length === swap.cell ? swap.value : fake && c % strip.length === fake.cell ? fake.value : s)}</span>`).join('')}</div>${spotted ? spotTag() : ''}</div>`;
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
  // snap), teaseWin (a crash and a rising sting), teaseLose (a sad trombone), win (the fanfare; index = tier), spot (a
  // Golden Onkey landing; index = how many have landed before it), cash (a cash-out chip; index = its place).
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
      } else if (kind === 'spot') {
        // A Golden Onkey landing: a bright two-note glint over a shimmer, higher for the second one.
        const lift = index ? 1.26 : 1;
        [1567.98, 2093].forEach((f, k) => tone(f * lift, now + k * .07, 'triangle', .06, .35));
        noise(now, .5, .04, 'highpass', 7000);
      } else if (kind === 'cash') {
        // A cash-out chip dealing in: a coin tick, rising with each one (index).
        tone(1318.51 * 1.12 ** index, now, 'square', .035, .09, { lp: 4000 });
        tone(2637 * 1.12 ** index, now + .03, 'sine', .03, .12);
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
    const frames = [{ opacity: 0, transform: 'translate(-50%, -50%) scale(.2) rotate(-8deg)' },
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
  // Diamond < banana < Onkey < Golden Onkey: each tier adds to the one below it.
  function celebrate(spin) {
    const tier = tierOf(spin);
    if (spin.net > 0) sound('win', tier);
    // Golden Onkeys spotted or doubling a line (not the three-of-a-kind jackpot, which is tier 4): golden sparkles out
    // of each one, at the same time as the line's own celebration rather than after it.
    const spotted = partsOf(spin).find((p) => p.kind === 'spotted' || p.kind === 'wild');
    if (spotted) $$('.slots-reel.spotted').forEach((el) => confetti?.(el, spotted.count > 1, { emoji: ['🌟', '✨'] }));
    if (!tier) return;
    const glass = $('.slots-glass'), cabinet = $('.slots-cabinet');
    const payout = `${money(spin.payout)} credits`;
    if (tier === 1) { confetti?.(glass, false, { emoji: ['💎', '✨'] }); return; }
    if (tier === 2) {
      confetti?.(glass, true, { emoji: ['🍌'] });
      shake(cabinet, 5, 450); rain(fxLayer(4000), emoji(['🍌']), 36, 900);
      return;
    }
    if (tier === 3) {
      const layer = fxLayer(5000);
      banner(layer, '/assets/onkey.png', 'ONKEY!', payout, 3200);
      flash(layer, 1); shake(cabinet, 10, 900);
      confetti?.(glass, true); setTimeout(() => confetti?.(glass, true), 500);
      rain(layer, image('/assets/onkey-logo.png', 'slots-fx-onkey'), 28, 1400);
      return;
    }
    const layer = fxLayer(8000);
    layer.classList.add('dim');
    banner(layer, '/assets/onkey-logo.png', 'GOLDEN ONKEY!!!', payout, 5600, 'golden');
    flash(layer, 3); shake(cabinet, 16, 1800);
    [0, 450, 900, 1500, 2200].forEach((ms) => setTimeout(() => confetti?.(glass, true), ms));
    rain(layer, image('/assets/onkey-logo.png', 'slots-fx-onkey golden'), 90, 3600);
  }

  // A Golden Onkey landing on the line: its reel flashes gold and a "Spotted!" tag pops up on it, with a glint. The
  // redraw after the spin keeps the animation going where it was (spotAt), so it plays once, smoothly.
  const SPOT_MS = 900, spotAt = [0, 0, 0];
  const spotTag = () => `<span class="slots-spot-tag" aria-hidden="true">Spotted!</span>`;
  function spotPop(el, elapsed) {
    if (elapsed >= SPOT_MS) return;
    const ring = el.animate([
      { boxShadow: 'inset 0 0 0 0 rgba(255, 210, 60, 0), 0 0 0 rgba(255, 196, 0, 0)' },
      { boxShadow: 'inset 0 0 0 8px rgba(255, 210, 60, .95), 0 0 46px rgba(255, 196, 0, .85)', offset: .25 },
      { boxShadow: 'inset 0 0 0 3px rgba(255, 210, 60, .8), 0 0 18px rgba(255, 196, 0, .45)' },
    ], { duration: SPOT_MS, easing: 'ease-out' });
    const tag = el.querySelector('.slots-spot-tag');
    const pop = tag?.animate([
      { opacity: 0, transform: 'translateX(-50%) translateY(14px) scale(.4)' },
      { opacity: 1, transform: 'translateX(-50%) translateY(-4px) scale(1.18)', offset: .35 },
      { opacity: 1, transform: 'translateX(-50%) translateY(0) scale(1)' },
    ], { duration: SPOT_MS * .6, easing: 'cubic-bezier(.2, .8, .3, 1.2)', fill: 'backwards' });
    [ring, pop].forEach((a) => { if (a) a.currentTime = elapsed; });
  }
  function landSpotted(i, before) {
    const el = $$('.slots-reel')[i];
    spotAt[i] = performance.now();
    sound('spot', before);
    if (!el) return;
    el.classList.add('spotted');
    if (!el.querySelector('.slots-spot-tag')) el.insertAdjacentHTML('beforeend', spotTag());
    spotPop(el, 0);
  }

  // Cubic Hermite: from `from`, cover `dist` in `dur` seconds, starting at velocity m0 / dur and ending at m1 / dur.
  const hermite = (from, dist, m0, m1) => (t) => from + m0 * (t * t * t - 2 * t * t + t) + dist * (-2 * t * t * t + 3 * t * t) + m1 * (t * t * t - t * t);
  // A snap with a little overshoot before it settles.
  const backOut = (t) => 1 + 2.70158 * (t - 1) ** 3 + 1.70158 * (t - 1) ** 2;
  // `only` is the one reel that spins on a hold (the other two stay where they are); null spins all three.
  function startMotion(only = null) {
    if (cameoAt) { window.FiveOnkey?.home(reelSpot(cameoAt.reel, cameoAt.cell), cameoAt.golden); cameoAt = null; } // the next spin: Onkey walks back
    const start = performance.now(), fast = turbo ? TURBO : 1, positions = reelsNow().map((value, i) => cellFor(i, value)), brakes = [];
    const stopped = [0, 1, 2].map((i) => only !== null && i !== only);
    const origin = [...positions];
    // order[k] is the reel that stops k-th (stopOrder(), set in settle()); last() is the one that decides the spin.
    let previous = start, frame = 0, targets = null, dues = null, ended = false, resolve, tease = null, nudge = false, cameo = null, walked = false, order = [0, 1, 2];
    const last = () => order[2];
    const clanked = [0, 0, 0];
    let lastClank = 0;
    const done = new Promise((r) => { resolve = r; });
    const finish = () => {
      // Stopped while Onkey was still on his way (the tab was left): his cell gets its symbol and he goes straight back.
      if (cameo !== null && blank[cameo] !== null) { blank[cameo] = null; window.FiveOnkey?.home(); }
      ended = true; cancelAnimationFrame(frame); tease?.quiet?.(); resolve();
    };
    // The reels are down with Onkey's cell (reel `cameo`) empty: he walks from the logo into it and becomes the
    // symbol, and only then is the spin over. The symbol is put in its cell unseen first, so he walks to exactly
    // where it's drawn and it takes his place without a jump. If he can't come (or takes too long) it just appears.
    function walkIn() {
      const i = cameo, cell = brakes[i].cell, golden = targets[i] === wildIndex();
      const hide = (on) => [0, 1, 2].forEach((k) => {
        const img = $$('.slots-strip')[i]?.children[k * cells() + cell]?.firstElementChild;
        if (img) img.style.visibility = on ? 'hidden' : '';
      });
      let sat = false;
      const sit = (came) => {
        if (sat || ended) return;
        sat = true; blank[i] = null;
        // A redraw while he walked left the cell empty: fill it again. Otherwise just show what's waiting there.
        if ($$('.slots-strip')[i]?.children[cells() + cell]?.firstElementChild) hide(false); else setCell(i, cell, targets[i]);
        if (came) { walked = true; cameoAt = { reel: i, cell, golden }; }
        if (golden) landSpotted(i, targets.filter((t, k) => k !== i && t === wildIndex()).length);
        else sound('stop', 2);
        finish();
      };
      if (golden) swapped[i] = { cell, value: targets[i] };
      setCell(i, cell, targets[i]); hide(true);
      const to = reelSpot(i, cell);
      if (!to || !window.FiveOnkey?.visit) { sit(false); return; }
      window.FiveOnkey.visit(to, sit, golden);
      setTimeout(() => sit(true), CAMEO_MAX_MS);
    }
    function paint() {
      $$('.slots-strip').forEach((strip, i) => {
        strip.style.transform = `translate3d(0,${70 - (cells() + wrap(positions[i])) * 140}px,0)`;
        strip.parentElement.classList.toggle('stopped', stopped[i]);
        strip.parentElement.classList.toggle('teasing', !!tease?.on && i === last() && !stopped[i]);
        strip.parentElement.classList.toggle('held', only !== null && i !== only);
        if (stopped[i] && targets) strip.parentElement.setAttribute('aria-label', data.symbols[targets[i]].name);
      });
      $('.slots-glass')?.classList.toggle('teasing', !!tease?.on && !stopped[last()]);
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
      if (tease && i === last()) {
        // Keep going flat out, slow to a crawl, then creep (longer and slower for bigger pairs) to stop half on the
        // matching symbol and half on its neighbour, teeter there to a heartbeat, and snap: forward or back onto the
        // match on a win; on a loss, back when the match was just short (the cell below) or forward off it when it
        // had crept just past (the cell above). Lining up the landing cell only adds full-speed spin.
        const lvl = tease.level, hold = .5 + lvl * .2, crawl = 2.6 - lvl * .25, creep = 1.5 + lvl * .15;
        const slowDur = 1 + lvl * .1, slowDist = (velocity + crawl) / 2 * slowDur, creepDur = 2 * creep / crawl;
        const teeterDur = 1.1 + lvl * .15, beats = lvl >= 3 ? 4 : 3, snapDur = .34;
        // The pair may be one the Golden Onkey makes; a win is any line (the Golden Onkey filling in too).
        const pair = pairSymbol(targets[order[0]], targets[order[1]]), win = lineOf(targets) !== -1;
        const strip = stripsNow()[i], n = strip.length;
        const below = (c) => strip[(c + 1) % n] === pair, above = (c) => strip[(c + n - 1) % n] === pair;
        const { cell, secret, dist } = landing(i, from, velocity * hold + slowDist + creep, win ? null : (c) => below(c) || above(c));
        const near = win || below(cell) || above(cell);
        const back = win || !near ? Math.random() < .5 : below(cell) && (!above(cell) || Math.random() < .5);
        // No cell of the result has the pair's symbol beside it on this strip: draw it in the neighbour it teeters on.
        if (decoy[i]) { setCell(i, decoy[i].cell, strip[decoy[i].cell]); decoy[i] = null; }
        if (!near) {
          decoy[i] = { cell: (cell + (back ? 1 : n - 1)) % n, value: pair };
          setCell(i, decoy[i].cell, pair);
        }
        const off = back ? .5 : -.5;  // where it teeters, from the landing cell
        const holdDist = dist + off - slowDist - creep, holdDur = holdDist / velocity, edge = from + dist + off;
        // A miss that still lands a Golden Onkey isn't a loss: it ends on its glint, not the trombone.
        tease.on = true; tease.won = win; tease.spotted = !win && targets[i] === wildIndex();
        window.FiveOnkey?.note('slots_tease'); // Onkey gasps along
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
      const dur = (1100 + order.indexOf(i) * 160) * fast / 1000, run = velocity * dur / 2;
      const { cell, secret, dist } = landing(i, from, run);
      if (i === cameo) { blank[i] = cell; setCell(i, cell, null); } // Onkey's cell arrives empty
      // A nudge: stop one symbol short (on something else, so it looks lost), wait, then bump onto the line.
      const strip = stripsNow()[i], before = strip[(cell + strip.length - 1) % strip.length];
      if (nudge && i === last() && before !== targets[i] && dist > 2) {
        const short = from + dist - 1;
        return { at: now, end: from + dist, cell, secret, segs: [
          { dur, at: hermite(from, dist - 1, velocity * dur, 0) },
          { dur: NUDGE_WAIT, at: () => short, wait: true },
          { dur: .3, at: (u) => short + backOut(u), bump: true },
        ] };
      }
      return { at: now, segs: [{ dur, at: hermite(from, dist, velocity * dur, 0) }], end: from + dist, cell, secret };
    }
    function tick(now) {
      if (ended) return;
      if (state.view !== 'slots') { finish(); return; }
      const dt = Math.min((now - previous) / 1000, .05); previous = now;
      positions.forEach((position, i) => {
        if (stopped[i]) return;
        const velocity = (14 + i) * Math.min((now - start) / 420, 1);
        // A swapped-in secret symbol goes back to its strip symbol once it has rolled out of view (1.5 cells shows).
        if (swapped[i] && !brakes[i]?.secret && position - origin[i] > 2.5) {
          setCell(i, swapped[i].cell, stripsNow()[i][swapped[i].cell]);
          swapped[i] = null;
        }
        if (decoy[i] && !brakes[i] && position - origin[i] > 3) {
          setCell(i, decoy[i].cell, stripsNow()[i][decoy[i].cell]);
          decoy[i] = null;
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
        if (sg.wait && !b.waited) {
          b.waited = true; sound('stop', i);
          $$('.slots-reel')[i]?.insertAdjacentHTML('beforeend', '<span class="slots-nudge-tag" aria-hidden="true">Nudge!</span>');
        }
        if (sg.bump && !b.bumped) { b.bumped = true; sound('snap'); }
        if (sg.snap && !b.snapped) {
          b.snapped = true; tease.teeter = false; sound('snap');
          const cabinet = $('.slots-cabinet');
          if (cabinet) shake(cabinet, 5, 260);
        }
        // Swap the secret symbol in while its cell is still out of view below the window.
        if (b.secret && !swapped[i] && i !== cameo && b.end - positions[i] < 2.5) {
          swapped[i] = { cell: b.cell, value: targets[i] };
          setCell(i, b.cell, targets[i]);
        }
        if (k === b.segs.length - 1 && u === 1) {
          positions[i] = b.end;
          stopped[i] = true; rest[i] = b.cell; sound('stop', i);
          if (targets[i] === wildIndex() && i !== cameo) landSpotted(i, stopped.filter((x, k) => x && k !== cameo && targets[k] === wildIndex()).length - 1);
          if (tease && i === last()) { tease.quiet?.(); if (!tease.spotted) sound(tease.won ? 'teaseWin' : 'teaseLose'); }
        }
      });
      paint();
      if (stopped.every(Boolean)) { if (cameo !== null) walkIn(); else finish(); }
      else frame = requestAnimationFrame(tick);
    }
    // The ratchet of a symbol passing the line: one stream for the machine, at most every 70 ms across the reels.
    function clank(i, now) {
      if (now - clanked[i] < 35 || now - lastClank < 70) return;
      clanked[i] = lastClank = now; sound('clank', i);
    }
    frame = requestAnimationFrame(tick);
    return {
      paint, cancel: finish,
      // Whether the last spin teased, and if so which reel and whether it won (for the flash after the redraw).
      cameo: () => walked, // whether Onkey walked in for this spin
      teased: () => (tease?.on && !tease.spotted ? { reel: last(), won: tease.won } : null),
      settle: (reels) => {
        targets = reels; order = only === null ? stopOrder(reels) : [...[0, 1, 2].filter((i) => i !== only), only];
        const stopAt = Math.max(performance.now(), start + 800 * fast), keyOf = (r) => data.symbols[r]?.key;
        const [a, b] = order.map((i) => reels[i]);
        // Decided from the first two reels to stop only (a Golden Onkey pairs with anything).
        const key = matches(a, b) ? keyOf(pairSymbol(a, b)) : null;
        if (key && Math.random() < (TEASE[key] || 0)) tease = { level: TEASE_LEVEL[key] || 0, on: false };
        // Onkey's walk-in takes the place of a tease or a nudge: into a Golden Onkey's cell when the result shows
        // one, else into his own symbol's, the deciding reel's when it's one of them.
        const spots = (key) => [order[2], order[1], order[0]].filter((i) => keyOf(reels[i]) === key);
        const gold = spots('golden'), own = spots('monkey');
        if (gold.length && Math.random() < GOLDEN_CAMEO_CHANCE) [cameo] = gold;
        else if (!gold.length && own.length && Math.random() < CAMEO_CHANCE) [cameo] = own;
        if (cameo !== null) tease = null;
        nudge = !tease && cameo === null && lineOf(reels) !== -1 && Math.random() < NUDGE_CHANCE;
        // A hold: only its reel is turning, so it just needs its own moment to stop.
        if (only !== null) { dues = []; dues[only] = stopAt + (tease ? 500 : 400 * fast); return done; }
        // When each reel starts braking, in stop order (they take 1.1, 1.26 and 1.42 s): 180 ms apart plus the pauses;
        // a teased last reel starts its run-in once the second has stopped. Two different symbols first means no
        // pair anywhere (stopOrder() puts one first), so the last reel follows close behind instead of making you wait.
        const bigWin = BIG_OPENING.includes(keyOf(lineOf(reels)));
        // Turbo (fast) shortens all of these, but not the tease's own run-in.
        const opening = (bigWin || Math.random() < .1 ? 700 + Math.random() * 500 : 0) * fast;
        const first = stopAt + opening, second = first + (180 + (PAUSE[keyOf(a)] || 0)) * fast;
        const third = tease ? second + 1260 * fast + 150 : matches(a, b) ? second + (180 + (PAUSE[keyOf(b)] || 0) * 1.5 + 150) * fast : second + 120 * fast;
        dues = [];
        [first, second, third].forEach((due, k) => { dues[order[k]] = due; });
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
    const expected = 1 / (m.win_chance || m.chances.reduce((a, c) => a + c, 0));
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
      ${rows.length ? `<ol class="slots-big-list">${rows.map((w, k) => `<li><span class="slots-big-rank">${k + 1}</span><span class="slots-big-who">${window.FiveCasino ? window.FiveCasino.who(w.bettor) : plainName(w.bettor)}</span><span class="slots-mini-reels" aria-label="${esc(w.reels.map((r) => data.symbols[r].name).join(', '))}">${w.reels.map(glyph).join('')}</span><span class="slots-big-pay"><b>+${money(w.net)}</b><small>${w.multiplier}× on ${money(w.stake)} · ${esc(fmt.date(w.created_ts * 1000))}</small></span></li>`).join('')}</ol>` : '<p class="muted">No wins yet this season.</p>'}
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
      <div class="slots-prizes">${rows.map((i) => `<div class="slots-prize ${win && lineSymbol(result) === i ? 'won' : ''}"><span class="slots-pay-left"><span class="slots-pay-symbol" aria-hidden="true">${glyph(i).repeat(3)}</span><span class="sr-only">Three ${esc(data.symbols[i].name)}</span><small>1 in ${fmt.n0(Math.round(1 / m.chances[i]))}${lines ? ` · <span class="slots-hits">you've hit ${fmt.n0(lines.hits[i])}</span>` : ''}</small></span><span><b>${m.triples[i]}×</b><small>${money(m.triples[i] * stake)} credits</small></span></div>`).join('')}</div>
      ${note ? `<p class="muted small slots-lines-note">${note}</p>` : ''}
      <details class="how"><summary>Every spin is independent.</summary><p>Each reel stops on a symbol at random; the chance beside each line is for all three reels matching it. Pairs and mixed symbols pay 0, unless the rare Golden Onkey is among them: it's wild, finishing any line it's part of and multiplying it (×2 for one, ×3 for two, never past 100×), and alone with no line it still pays 2× when spotted; three of them are the 100× jackpot, the top prize. Average return: ${m.rtp}% over many spins. Results settle immediately and cannot be cancelled.</p><p>Slot results have their own season totals. Match-betting ROI stays separate. Slots do not earn shop bananas.</p></details>
    </aside>`;
  }
  // A win's cash-outs side by side, one chip per part (the line; the Golden Onkey doubling it, or spotted on its own),
  // and with more than one a total. A fresh win deals them in quickly one after another (CSS, --k) with a coin tick each and counts the total
  // up (cashIn()); otherwise they just sit there.
  function cashouts(spin) {
    const parts = partsOf(spin), w = wildIndex(), name = (i) => esc(data.symbols[i].name);
    const chip = (k, cls, icons, label, amount) => `<span class="slots-cash ${cls}" style="--k:${k}"><span class="slots-cash-icons" aria-hidden="true">${icons}</span><span class="slots-cash-text"><small>${label}</small><b>+${money(amount)}</b></span></span>`;
    const chips = parts.map((p, k) => {
      if (p.kind === 'spotted') return chip(k, 'spotted', glyph(w), `Golden Onkey spotted · ${p.mult}×`, spin.stake * p.mult);
      if (p.kind === 'wild') return chip(k, 'spotted wild', glyph(w).repeat(p.count), `Golden Onkey${p.count > 1 ? 's' : ''} wild · line ×${p.factor}${p.capped ? ', capped at 100×' : ''}`, spin.stake * p.mult);
      if (p.symbol === w) return chip(k, 'line jackpot', glyph(w).repeat(3), `Three Golden Onkeys · ${p.mult}×`, spin.stake * p.mult);
      return chip(k, `line${p.wild ? ' wild' : ''}`, glyph(p.symbol).repeat(3 - p.wild) + glyph(w).repeat(p.wild),
        `${name(p.symbol)} line${p.wild ? ' (wild)' : ''} · ${p.mult}×`, spin.stake * p.mult);
    });
    const total = parts.length > 1 ? `<span class="slots-cash-join" style="--k:${parts.length}" aria-hidden="true">=</span><span class="slots-cash total" style="--k:${parts.length}"><span class="slots-cash-text"><small>${spin.multiplier}× in all</small><b data-count="${spin.payout}">+${money(spin.payout)}</b></span></span>` : '';
    const joined = chips.map((c, k) => (k ? `<span class="slots-cash-join" style="--k:${k - .5}" aria-hidden="true">+</span>` : '') + c).join('');
    return `<div class="slots-cashouts">${joined}${total}</div>`;
  }
  const CASH_GAP = 120;
  function cashIn() {
    const chips = $$('.slots-result.fresh .slots-cash');
    chips.forEach((el, k) => setTimeout(() => sound('cash', k), k * CASH_GAP));
    const total = $('.slots-result.fresh [data-count]');
    if (!total) return;
    const to = Number(total.dataset.count), begin = performance.now() + (chips.length - 1) * CASH_GAP, ms = 520;
    const step = (now) => {
      if (!total.isConnected) return;
      const u = Math.max(0, Math.min((now - begin) / ms, 1));
      total.textContent = `+${money(Math.round(to * (1 - (1 - u) ** 3)))}`;
      if (u < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
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
    return `<div class="slots-well slots-readout">${cell('Credits', state.me ? money(displayBalance()) : '–')}${cell('Bet', money(betNow()))}${cell('Win', result && !busy ? money(result.payout) : '0')}</div>`;
  }
  // The Auto key, in a well of its own left of the Spin button: lit while Auto is on.
  const autoLabel = () => (auto ? `Auto spin is on${autoCap ? `, ${autoLeft} left` : ''}. Turn it off`
    : `Auto spin: ${autoCap ? `${autoCap} spins` : 'keep spinning until turned off'}`);
  const autoText = () => (auto ? (autoCap ? autoLeft : 'on') : 'off');
  const autoKey = () => `<button type="button" class="slots-auto" id="slot-auto" aria-pressed="${auto}" aria-label="${autoLabel()}" title="${autoLabel()}"><small>Auto</small><b>${autoText()}</b></button>`;
  function paintAuto() {
    const b = $('#slot-auto');
    if (!b) return;
    b.setAttribute('aria-pressed', String(auto)); b.setAttribute('aria-label', autoLabel()); b.title = autoLabel();
    b.querySelector('b').textContent = autoText();
  }
  // The gear at the marquee's left and its menu: Auto's two settings.
  const GEAR = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3.2"/><path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7"/><circle cx="12" cy="12" r="6.8"/></svg>';
  const capText = (n) => (n ? String(n) : 'No limit');
  const settingsMenu = () => `<button class="slots-sound slots-gear" id="slot-settings" aria-haspopup="true" aria-expanded="${settingsOpen}" aria-controls="slot-settings-menu" aria-label="Slots settings" title="Settings">${GEAR}</button>
    <div class="slots-settings" id="slot-settings-menu" role="group" aria-label="Auto spin settings" ${settingsOpen ? '' : 'hidden'}>
      <div class="slots-set-row"><span><b>Auto takes holds</b><small>Each hold costs the stake again.</small></span>
        <button type="button" class="slots-switch" id="slot-auto-hold" role="switch" aria-checked="${autoHold}" aria-label="Auto takes holds"><i></i></button></div>
      <div class="slots-set-row stack"><span><b>Auto stops after</b><small>How many spins a run of Auto lasts.</small></span>
        <div class="slots-caps" role="group" aria-label="Auto spin limit">${AUTO_CAPS.map((n) => `<button type="button" data-slot-cap="${n}" aria-pressed="${n === autoCap}">${capText(n)}</button>`).join('')}</div></div>
    </div>`;
  function showSettings(open) {
    settingsOpen = open;
    const menu = $('#slot-settings-menu');
    if (menu) menu.hidden = !open;
    $('#slot-settings')?.setAttribute('aria-expanded', String(open));
  }
  const turboLabel = () => (turbo ? 'Turbo is on: faster spins. Turn it off' : 'Turbo: faster spins');
  const turboKey = () => `<button type="button" class="slots-auto" id="slot-turbo" aria-pressed="${turbo}" aria-label="${turboLabel()}" title="${turboLabel()}"><small>Turbo</small><b>${turbo ? 'on' : 'off'}</b></button>`;
  // The Hold button on the bottom edge of the reel window while a hold is on offer: what it costs and what it's after.
  function holdBtn() {
    const h = holdOffer();
    if (!h) return '';
    const short = state.me.balance < result.stake, odds = 1 / h.chance;
    const line = short ? 'Not enough credits' : `${money(result.stake)} to respin · 1 in ${odds < 10 ? odds.toFixed(1) : Math.round(odds)} pays ${money(result.stake * h.mult)}`;
    const label = `Hold the two ${data.symbols[h.symbol].name} symbols and respin the last reel for ${result.stake} credits`;
    return `<button type="button" class="slots-hold" id="slot-hold" aria-keyshortcuts="H" aria-label="${esc(label)}" ${short ? 'disabled' : ''}><b>Hold <span aria-hidden="true">${glyph(h.symbol)}${glyph(h.symbol)}</span></b><small>${line}</small></button>`;
  }
  // The tag over the Spin button while the day's free spins last. A phone has no Spin button, so there a second copy
  // (`reels`) sits over the top of the reel window, and style.css shows whichever belongs.
  const freeTag = (reels) => (freeLeft() ? `<span class="slots-free${reels ? ' on-reels' : ''}">${freeLeft()} free spin${freeLeft() === 1 ? '' : 's'}</span>` : '');
  function view() {
    syncOwner();
    if (!data || (data.me?.name || null) !== owner) {
      load().then(() => { if (state.view === 'slots') draw(); }).catch(() => {
        const el = $('#slot-loading'); if (el) el.textContent = 'Could not load slots. Open this tab again to retry.';
      });
      return '<section class="card" id="slot-loading">Loading slots…</section>';
    }
    const m = data.machines.find((g) => g.key === 'jackpot'), locked = busy || !!pending;
    const free = freeLeft(), insufficient = state.me && !free && state.me.balance < stake;
    const win = !busy && result?.net > 0, tier = busy ? 0 : tierOf(result);
    const message = result ? result.payout ? `${money(result.payout)} credits returned` : 'No winning line' : 'Ready when you are';
    const cashing = !busy && partsOf(result).length;
    const detail = result ? `${signed(result.net)} credits net${holdOffer() ? '. Hold the pair, or spin on.' : ''}` : 'Three matching symbols on the centre line pays. Space spins.';
    return `<div class="slots-layout"><section class="slots-cabinet ${win ? 'slots-win' : ''} ${tier ? `slots-tier-${tier}` : ''} ${busy ? 'slots-busy' : ''}"><div class="slots-leds" aria-hidden="true">${leds()}</div><div class="slots-body">
        <div class="slots-marquee">${settingsMenu()}<h2>Slots</h2><button class="slots-sound" id="slot-sound" aria-pressed="${!muted}" aria-label="${soundLabel()}" title="${soundLabel()}">${speaker(muted)}</button></div>
        <div class="slots-stage"><div class="slots-window">${freeTag(true)}<div class="slots-glass"><div class="slots-reels ${busy ? 'spinning' : ''}" aria-label="${busy ? 'Reels spinning' : 'Reel result'}" aria-busy="${busy}">${reelsNow().map(reel).join('')}</div><span class="slots-line-arrow left" aria-hidden="true">▸</span><span class="slots-line-arrow right" aria-hidden="true">◂</span>${holdBtn()}</div>${lever(insufficient)}
</div>
          <div class="slots-result${cashing && freshId === result.id ? ' fresh' : ''}" role="status" aria-live="polite">${cashing ? cashouts(result) : `<b>${busy ? 'Spinning…' : esc(message)}</b>`}<span>${busy ? 'Let them roll.' : esc(detail)}</span></div>
        </div>
        <div class="slots-deck"><span class="slots-screw" aria-hidden="true"></span><span class="slots-screw" aria-hidden="true"></span><span class="slots-screw" aria-hidden="true"></span><span class="slots-screw" aria-hidden="true"></span>
          <div class="slots-well slots-stakes" role="group" aria-label="Credits per spin">${data.stakes.map((s) => `<button type="button" data-slot-stake="${s}" aria-pressed="${s === stake}" aria-label="Bet ${s} credits" ${locked ? 'disabled' : ''}><small>Bet</small><b>${s}</b></button>`).join('')}</div>
          ${readout()}
          ${state.me ? `<div class="slots-well slots-auto-well">${autoKey()}${turboKey()}</div>` : ''}<div class="slots-well slots-spin-well">${freeTag()}<div class="slots-spin-ring">${state.me ? `<button class="slots-spin" id="slot-spin" aria-keyshortcuts="Space" ${busy || (insufficient && !pending) ? 'disabled' : ''} aria-label="${busy ? 'Spinning' : pending ? 'Check last spin' : free ? `Free spin, ${free} left today` : `Spin for ${stake} credits`}"><span>${busy ? '···' : pending ? 'Check' : free ? 'Free' : 'Spin'}</span>${pending && !busy ? '<small>last spin</small>' : ''}</button>` : '<button class="slots-spin" data-signin><span>Sign in</span><small>to spin</small></button>'}</div></div>
        </div>
        <p class="slots-error" role="alert">${esc(error || (insufficient && !pending ? 'Not enough credits. Choose a smaller stake.' : ''))}</p>
      </div></section>${paytable(m, win)}</div>
      <div class="slots-lower">${history()}${season(m)}${bigWins()}</div>`;
  }
  // One spin, or with `opts.hold` (the spin on offer) a hold of it: the same flow, with one reel turning.
  async function spin(opts) {
    if (busy || !state.me) return;
    const fresh = !pending, held = fresh && opts?.hold ? opts.hold : null;
    if (fresh) {
      const bytes = new Uint8Array(16); crypto.getRandomValues(bytes);
      const id = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
      // One of the day's free spins while any are left: the server gives its stake.
      remember(held ? { stake: held.stake, request_id: id, hold: held.id, reel: held.hold.reel }
        : freeLeft() ? { stake: data.daily.stake, request_id: id, daily: true } : { stake, request_id: id });
    }
    const name = owner, request = pending;
    let landed = null;
    // The server pays the spin before the reels stop, so until they do the balance shown (here and in the top bar)
    // is held at what it was less the stake: it can't give the result away. A retried spin's stake was already taken,
    // and a free spin's is the house's.
    holdBalance(fresh && !request.daily ? state.me.balance - request.stake : state.me.balance);
    busy = true; error = ''; sound('start'); draw();
    $('.slots-lever')?.classList.add('pulled');
    // A hold turns only its reel, as long as the spin it holds is still the one showing (not after a reload).
    const rolling = motion = startMotion(request.hold != null && result?.id === request.hold ? request.reel : null);
    try {
      const out = request.hold != null
        ? await api('/api/slots/hold', { method: 'POST', body: JSON.stringify({ spin: request.hold, request_id: request.request_id }) })
        : await api('/api/slots/spin', { method: 'POST', body: JSON.stringify(request) });
      await rolling.settle(out.spin.reels);
      if (owner !== name) return;
      result = landed = out.spin; remember(null);
      if (state.me?.name === name) state.me.balance = out.balance;
      releaseBalance(); // the reels have stopped: the credits count to the real balance
      await Promise.all([load(), loadMe()]);
    } catch (e) {
      if (owner === name) {
        if (e.status >= 400 && e.status < 500) remember(null);
        error = pending ? 'The result could not be confirmed. Check last spin to recover it without paying twice.' : e.message;
      }
    } finally {
      const teased = rolling.teased(), walkedIn = rolling.cameo();
      rolling.cancel(); motion = null; busy = false;
      releaseBalance();
      // Auto: after a pause, the hold on offer if Auto takes them and the credits are there, else the next spin,
      // unless something stops it: an error, leaving, a capped run's last spin, or too few credits.
      if (auto) {
        if (autoCap && request.hold == null) autoLeft -= 1;
        const takes = autoHold && landed?.hold && state.me?.balance >= landed.stake;
        const again = () => auto && !busy && state.view === 'slots';
        if (!landed || owner !== name || state.view !== 'slots' || document.hidden) auto = false;
        else if (takes) setTimeout(() => { if (again()) spin(holdOffer() && result === landed ? { hold: landed } : null); }, quick(AUTO_HOLD_GAP_MS));
        else if ((autoCap && autoLeft <= 0) || (!freeLeft() && state.me?.balance < stake)) auto = false;
        else setTimeout(() => { if (again()) spin(); }, tierOf(landed) ? AUTO_BIG_GAP_MS : quick(landed.payout ? AUTO_WIN_GAP_MS : AUTO_GAP_MS));
      }
      if (state.view === 'slots') {
        if (landed) freshId = landed.id;
        draw(); $('#slot-spin')?.focus();
        if (landed) { cashIn(); freshId = null; }
        if (landed && teased) $$('.slots-reel')[teased.reel]?.classList.add(teased.won ? 'tease-won' : 'tease-lost');
        if (landed) {
          celebrate(landed);
          const line = lineSymbol(landed), sym = data.symbols[line ?? landed.reels[0]] || {};
          // He walked in and the line still lost: he says so himself instead of the usual word on a losing spin.
          if (walkedIn && !landed.payout) window.FiveOnkey?.note('slots_cameo');
          else window.FiveOnkey?.note('slots', { stake: landed.stake, payout: landed.payout, multiplier: landed.multiplier,
            symbol: sym.name, golden: !!sym.secret && landed.payout > 0,
            spotted: partsOf(landed).some((p) => p.kind === 'spotted'), wild: partsOf(landed).find((p) => p.kind === 'wild')?.factor });
        }
      }
    }
  }
  function bind(viewEl) {
    $$('[data-slot-stake]', viewEl).forEach((button) => button.addEventListener('click', () => {
      stake = Number(button.dataset.slotStake); error = ''; draw();
      window.FiveOnkey?.note('slots_stake', { stake });
      $(`[data-slot-stake="${stake}"]`)?.focus();
    }));
    $('#slot-spin', viewEl)?.addEventListener('click', spin);
    $('#slot-lever', viewEl)?.addEventListener('click', spin);
    $('#slot-hold', viewEl)?.addEventListener('click', (e) => {
      e.stopPropagation(); // not a tap on the reel window, which spins on a phone
      if (holdOffer()) spin({ hold: result });
    });
    // Switching Auto on spins straight away; switching it off lets the spin under way finish.
    $('#slot-auto', viewEl)?.addEventListener('click', () => {
      if (!auto && !busy && $('#slot-spin')?.disabled) return; // nothing to spin with
      auto = !auto; autoLeft = auto ? autoCap : 0; paintAuto();
      if (auto && !busy) spin();
    });
    $('#slot-settings', viewEl)?.addEventListener('click', () => showSettings(!settingsOpen));
    $('#slot-auto-hold', viewEl)?.addEventListener('click', (e) => {
      autoHold = !autoHold; localStorage.setItem('fs.slotsAutoHold', autoHold ? '1' : '0');
      e.currentTarget.setAttribute('aria-checked', String(autoHold));
    });
    // A new limit starts counting from now, on a run under way too.
    $$('[data-slot-cap]', viewEl).forEach((button) => button.addEventListener('click', () => {
      autoCap = Number(button.dataset.slotCap); localStorage.setItem('fs.slotsAutoCap', String(autoCap));
      autoLeft = auto ? autoCap : 0;
      $$('[data-slot-cap]').forEach((b) => b.setAttribute('aria-pressed', String(b === button)));
      paintAuto();
    }));
    $('#slot-turbo', viewEl)?.addEventListener('click', (e) => {
      turbo = !turbo; localStorage.setItem('fs.slotsTurbo', turbo ? '1' : '0');
      const b = e.currentTarget;
      b.setAttribute('aria-pressed', String(turbo)); b.setAttribute('aria-label', turboLabel()); b.title = turboLabel();
      b.querySelector('b').textContent = turbo ? 'on' : 'off';
    });
    // On a phone (style.css's phone block) there's no Spin button or lever to see: tapping the reel window spins. It
    // goes through the hidden Spin button, so a spin under way, too few credits and signing in behave the same.
    $('.slots-glass', viewEl)?.addEventListener('click', () => {
      if (matchMedia('(max-width: 640px)').matches) $('.slots-spin', viewEl)?.click();
    });
    $('#slot-sound', viewEl)?.addEventListener('click', (e) => {
      muted = !muted; localStorage.setItem('fs.slotsMuted', muted ? '1' : '0');
      const b = e.currentTarget;
      b.innerHTML = speaker(muted); b.setAttribute('aria-pressed', String(!muted)); b.setAttribute('aria-label', soundLabel()); b.title = soundLabel();
    });
    if (state.view === 'slots') motion?.paint();
    // A Golden Onkey that has only just landed carries on its pop where it was before the redraw.
    $$('.slots-reel.spotted', viewEl).forEach((el) => {
      const i = $$('.slots-reel', viewEl).indexOf(el);
      if (i >= 0) spotPop(el, performance.now() - spotAt[i]);
    });
  }
  return { init, load, view, bind };
})();
