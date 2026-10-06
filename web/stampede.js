/* Onkey Stampede: the Casino's 5 x 4 video slot (fivestack/stampede.py). The server plays the whole spin, free spins
   and hold and spin included, and pays it before the page shows anything; this page only plays it back. */
window.FiveStampede = (() => {
  'use strict';
  let state, $, $$, api, draw, esc, fmt, loadMe, confetti, plainName, holdBalance, releaseBalance, displayBalance;
  let data = null, owner = null, busy = false, error = '', pending = null, last = null, auto = 0, autoLeft = 0;
  let stake = Number(localStorage.getItem('fs.stStake')) || 10;
  let turbo = localStorage.getItem('fs.stTurbo') === '1', muted = localStorage.getItem('fs.stMuted') === '1';
  let hurry = false; // the presentation is being skipped through (Spin or Space pressed while it plays)
  let bonus = false; // a bonus (hold and spin, free spins, the pick) is playing: Skip leaves it at its own pace
  let nonstop = localStorage.getItem('fs.stNonstop') === '1'; // Auto carries on through bonuses and big wins
  // What the reels show right now: a grid of five columns of four symbols, fireball values and free-spin wild
  // multipliers by "c-r", and the wins to cycle through once a spin has landed.
  let shown = null;
  // The jackpots as shown: multiples of the stake, counted up to the server's each time they're fetched.
  let pots = null, potTarget = null, potTimer = null, potTick = null;
  let meterShown = 0; // the fire meter as drawn (it fills spark by spark during a spin)
  const AUTO_STEPS = [0, 10, 25, 50]; // the Auto button cycles through these
  // Big-win tiers by multiple of the stake (a jackpot always gets its own show).
  const TIERS = [[50, 'Epic win', 3], [25, 'Mega win', 2], [10, 'Big win', 1]];
  const SPEED = 26; // cells a second at full speed
  const storageKey = (name) => `fs.stampedeSpin.${name.toLowerCase()}`;
  const M = () => data.machine;
  const SYM = (i) => M().symbols[i];
  const idx = (key) => M().symbols.findIndex((s) => s.key === key);
  const sleep = (ms) => new Promise((r) => setTimeout(r, hurry ? Math.min(ms, 60) : ms));
  const T = (ms) => (turbo ? ms * 0.55 : ms);
  // Credits as the machine pays them: to the cent under 100 (a shape at a small bet pays 0.4), whole above.
  const money = (n) => {
    const v = Math.round(n * 100) / 100;
    return Math.abs(v) >= 100 || Number.isInteger(v) ? fmt.credits(v) : v.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 2 });
  };
  const how = (summary, more) => `<details class="how"><summary>${summary}</summary><div class="how-body">${more}</div></details>`;

  function init(ctx) {
    ({ state, $, $$, api, draw, esc, fmt, loadMe, confetti, plainName, holdBalance, releaseBalance, displayBalance } = ctx);
    document.addEventListener('keydown', (e) => {
      if (state.view !== 'stampede' || e.code !== 'Space' || e.repeat || e.ctrlKey || e.altKey || e.metaKey) return;
      if (e.target.closest?.('input, textarea, select, button, summary, a, [contenteditable], .modal')) return;
      e.preventDefault();
      press();
    });
    document.addEventListener('visibilitychange', () => { if (document.hidden) { auto = 0; autoLeft = 0; } });
  }
  function syncOwner() {
    const name = state.me?.name || null;
    if (owner === name) return;
    owner = name; data = null; error = ''; pending = null; auto = 0; autoLeft = 0;
    try { pending = name ? JSON.parse(sessionStorage.getItem(storageKey(name))) : null; } catch (_) { /* unavailable */ }
    if (pending) stake = pending.stake;
  }
  async function load() {
    syncOwner();
    const name = owner, next = await api('/api/stampede');
    if (owner !== name) return;
    data = next;
    if (!M().stakes.includes(stake)) stake = M().stakes[2];
    if (!pots) { pots = Object.fromEntries(data.pots.map((p) => [p.key, p.size])); }
    if (!busy) { meterShown = data.meter?.heat || 0; vaultShown = data.jackpot; keysShown = data.keys?.stakes.length || 0; }
    feedSeen = Math.max(feedSeen, ...(data.feed || []).map((f) => f.id));
    potTarget = Object.fromEntries(data.pots.map((p) => [p.key, p.size]));
    if (!shown) shown = { grid: M().strips.map((s, c) => win4(s, [7, 21, 40, 12, 33][c])), values: {}, wilds: {}, wins: [] };
    startPots();
  }
  function remember(value) {
    pending = value;
    try {
      if (value) sessionStorage.setItem(storageKey(owner), JSON.stringify(value));
      else sessionStorage.removeItem(storageKey(owner));
    } catch (_) { /* the in-memory reference still protects retries */ }
  }
  const win4 = (strip, stop) => [0, 1, 2, 3].map((r) => strip[(stop + r) % strip.length]);

  // ---- sound ------------------------------------------------------------------------------------------------------
  // Clips (web/assets/stampede/sfx/picks.json: `file`, relative to that folder, `role`, and `october` for one heard
  // only in October) play for their role, a random one each time: the squad's picks for wins, the Stampede, the bonus
  // start and music and the long count-up, the megawin track for Epic wins and the Major and Grand, and Onkey's own
  // recordings as his encore after those. Everything else is synthesized to fit the machine: wooden reel thuds and
  // ticks, crackling fireballs, a crystal chime for spikes, and a jungle-drum groove if the bonus music won't load.
  const SFX = (() => {
    let ctx = null, clips = null, loading = null, noiseBuf = null, master = null;
    const loops = new Set();
    function ready() {
      if (muted) return null;
      try {
        if (!ctx) {
          ctx = new (window.AudioContext || window.webkitAudioContext)();
          // One gentle compressor for everything, so stacked hits don't clip.
          master = ctx.createDynamicsCompressor();
          master.threshold.value = -14; master.ratio.value = 4;
          master.connect(ctx.destination);
        }
        ctx.resume().catch(() => {});
      } catch (_) { return null; }
      loading ||= (async () => {
        const got = {};
        try {
          const list = await (await fetch('/assets/stampede/sfx/picks.json')).json();
          const bufs = {};
          await Promise.all([...new Set(list.map((p) => p.file))].map(async (file) => {
            try { bufs[file] = await ctx.decodeAudioData(await (await fetch(`/assets/stampede/sfx/${file}`)).arrayBuffer()); } catch (_) { /* skipped */ }
          }));
          list.forEach((p) => { if (bufs[p.file]) (got[p.role] ||= []).push({ buf: bufs[p.file], october: !!p.october }); });
        } catch (_) { /* no clips */ }
        clips = got;
      })();
      return ctx;
    }
    function track(handle) { loops.add(handle); return handle; }
    // One win sound at a time: a new one cuts the last short, so free spins' wins don't pile up.
    const WIN_ROLES = new Set(['win_small', 'win_medium', 'win_big', 'win_epic']);
    const WIN_GAIN = 0.4; // the win sounds play at 40% of their recorded level
    let lastWin = null;
    function clip(role, { gain = 0.85, loop = false } = {}) {
      if (WIN_ROLES.has(role)) gain *= WIN_GAIN;
      const list = (clips?.[role] || []).filter((c) => !c.october || data?.october);
      if (!list.length) return null;
      const src = ctx.createBufferSource(), g = ctx.createGain();
      src.buffer = list[Math.floor(Math.random() * list.length)].buf; src.loop = loop; g.gain.value = gain;
      src.connect(g).connect(master); src.start();
      const handle = { duration: src.buffer.duration / (src.playbackRate.value || 1), stop(fade = 0.4) {
        try { g.gain.setTargetAtTime(0, ctx.currentTime, fade / 3); src.stop(ctx.currentTime + fade + 0.1); } catch (_) { /* stopped */ }
        loops.delete(handle);
      } };
      src.onended = () => loops.delete(handle);
      return track(handle);
    }
    function tone(f, at, type, level, len, { glide, lp, attack = 0.008 } = {}) {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = type; o.frequency.setValueAtTime(f, at);
      if (glide) o.frequency.exponentialRampToValueAtTime(glide, at + len);
      let head = o;
      if (lp) { const fl = ctx.createBiquadFilter(); fl.type = 'lowpass'; fl.frequency.value = lp; o.connect(fl); head = fl; }
      g.gain.setValueAtTime(0, at); g.gain.linearRampToValueAtTime(level, at + attack); g.gain.exponentialRampToValueAtTime(0.001, at + len);
      head.connect(g).connect(master); o.start(at); o.stop(at + len + 0.05);
    }
    function noise(at, len, level, type, freq, sweep, q = 1) {
      if (!noiseBuf) {
        noiseBuf = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate);
        const d = noiseBuf.getChannelData(0);
        for (let k = 0; k < d.length; k++) d[k] = Math.random() * 2 - 1;
      }
      const s = ctx.createBufferSource(), f = ctx.createBiquadFilter(), g = ctx.createGain();
      s.buffer = noiseBuf; s.loopStart = Math.random(); f.type = type; f.Q.value = q; f.frequency.setValueAtTime(freq, at);
      if (sweep) f.frequency.exponentialRampToValueAtTime(sweep, at + len);
      g.gain.setValueAtTime(0.0001, at); g.gain.linearRampToValueAtTime(level, at + Math.min(0.02, len / 4)); g.gain.exponentialRampToValueAtTime(0.001, at + len);
      s.connect(f).connect(g).connect(master); s.start(at, Math.random()); s.stop(at + len + 0.05);
    }
    // Drums: a hand drum (bongo / conga, `pitch` Hz), a deep kick and a shaker.
    const drum = (at, pitch, level = 0.25) => { tone(pitch * 1.6, at, 'sine', level, 0.22, { glide: pitch }); noise(at, 0.03, level * 0.35, 'bandpass', pitch * 6, null, 2); };
    const kick = (at, level = 0.4) => tone(120, at, 'sine', level, 0.3, { glide: 42 });
    const shaker = (at, level = 0.05) => noise(at, 0.06, level, 'highpass', 6500);
    // A bright mallet note (marimba-like): the jungle voice of the wins.
    const mallet = (f, at, level = 0.1) => { tone(f, at, 'sine', level, 0.45); tone(f * 4, at, 'sine', level * 0.25, 0.08); };
    const crackle = (at, len, n, level = 0.05) => { for (let j = 0; j < n; j++) noise(at + Math.random() * len, 0.015, level * (0.5 + Math.random()), 'highpass', 2500 + Math.random() * 3000); };
    const PENTA = [392, 440, 523.25, 587.33, 659.25, 783.99, 880, 1046.5, 1174.66, 1318.51, 1567.98];
    // The bonus groove: a two-bar jungle pattern at 128 bpm, scheduled a little ahead and looped until stopped.
    function groove() {
      const beat = 60 / 128 / 2; // eighth notes
      let next = ctx.currentTime + 0.05, step = 0, live = true;
      const PATTERN = [
        ['k', 'h'], ['s'], ['l', 's'], ['h'], ['k', 's'], ['l'], ['h', 's'], ['l', 's'],
        ['k', 'h'], ['s'], ['l', 's'], ['k'], ['k', 's'], ['h'], ['l', 'h'], ['s', 'm'],
      ];
      const timer = setInterval(() => {
        if (!live) return;
        while (next < ctx.currentTime + 0.25) {
          for (const p of PATTERN[step % PATTERN.length]) {
            if (p === 'k') kick(next, 0.3);
            if (p === 'h') drum(next, 330, 0.16);
            if (p === 'l') drum(next, 220, 0.18);
            if (p === 's') shaker(next);
            if (p === 'm') mallet(PENTA[3 + Math.floor(Math.random() * 6)], next, 0.06);
          }
          next += beat; step += 1;
        }
      }, 60);
      return track({ stop() { live = false; clearInterval(timer); loops.delete(this); } });
    }
    // Every role's sound. `k` varies a sound by the reel or the count it's for.
    function synth(role, k = 0) {
      const now = ctx.currentTime;
      switch (role) {
        case 'spin': // a lever thunk and the reels whooshing off
          tone(110, now, 'triangle', 0.18, 0.15, { glide: 60 }); noise(now + 0.03, 0.5, 0.07, 'bandpass', 400, 2200, 0.8); break;
        case 'tick': // a symbol passing the window while the reels turn
          noise(now, 0.02, 0.03, 'bandpass', 2200 + k * 300, null, 4); tone(500 + k * 40, now, 'triangle', 0.012, 0.03); break;
        case 'reel_stop': // a wooden thud
          tone(150 - k * 12, now, 'sine', 0.24, 0.16, { glide: 60 }); noise(now, 0.05, 0.08, 'bandpass', 900 - k * 60, null, 2); break;
        case 'scatter': // a crystal chime, rising with each spike
          [1567.98, 2093, 2637].forEach((f, j) => tone(f * (1 + k * 0.06), now + j * 0.05, 'sine', 0.06, 0.7)); noise(now, 0.6, 0.025, 'highpass', 8000); break;
        case 'fireball': // a whoosh catching fire
          noise(now, 0.35, 0.09, 'bandpass', 500, 3500, 1.4); tone(90 + k * 12, now, 'sawtooth', 0.04, 0.3, { glide: 180, lp: 600 }); crackle(now + 0.1, 0.35, 6); break;
        case 'lock': // a fireball dropping in during hold and spin
          drum(now, 260 + (k % 6) * 30, 0.2); noise(now, 0.25, 0.05, 'bandpass', 1200, 3000); crackle(now + 0.05, 0.25, 4); break;
        case 'tease': // a rising drone with a pulse in it
          tone(98, now, 'sawtooth', 0.035, 1.6, { glide: 196, lp: 900, attack: 0.3 }); for (let j = 0; j < 8; j++) drum(now + j * 0.2, 180, 0.08 + j * 0.01); break;
        case 'win_small':
          [4, 6, 7].forEach((n, j) => mallet(PENTA[n], now + j * 0.07, 0.09 * WIN_GAIN)); break;
        case 'win_medium':
          [2, 4, 5, 7, 9].forEach((n, j) => mallet(PENTA[n], now + j * 0.08, 0.1 * WIN_GAIN)); kick(now, 0.25 * WIN_GAIN); drum(now + 0.32, 330, 0.15 * WIN_GAIN); break;
        case 'win_big':
          for (let j = 0; j < 12; j++) mallet(PENTA[(j * 2) % PENTA.length], now + j * 0.08, 0.1 * WIN_GAIN);
          for (let j = 0; j < 6; j++) kick(now + j * 0.2, 0.3 * WIN_GAIN); noise(now + 0.6, 1.4, 0.05 * WIN_GAIN, 'highpass', 7000); break;
        case 'count': tone(2093 + (k % 7) * 80, now, 'sine', 0.03, 0.06); break;
        case 'bonus_start': // a drum roll speeding up into a hit
          for (let j = 0; j < 14; j++) drum(now + 0.6 * (1 - Math.pow(1 - j / 14, 1.6)), 240 + j * 6, 0.1 + j * 0.01);
          kick(now + 0.65, 0.45); [0, 2, 4].forEach((n) => mallet(PENTA[n + 3], now + 0.65, 0.1)); noise(now + 0.65, 0.9, 0.05, 'highpass', 6000); break;
        case 'stampede': // galloping hooves and a rumble
          for (let j = 0; j < 12; j++) { const t = now + Math.floor(j / 3) * 0.32 + (j % 3) * 0.08; drum(t, 110 + (j % 3) * 25, 0.22); }
          noise(now, 1.4, 0.08, 'lowpass', 220, 120); break;
        case 'stamp': drum(now, 200, 0.3); kick(now, 0.2); break;
        case 'jackpot':
          for (let j = 0; j < 16; j++) mallet(PENTA[j % PENTA.length] * (j > 10 ? 2 : 1), now + j * 0.07, 0.1);
          for (let j = 0; j < 8; j++) kick(now + j * 0.18, 0.3); noise(now, 2.4, 0.05, 'highpass', 7000); break;
        case 'inferno': // a roaring burst of fire
          noise(now, 1.4, 0.18, 'bandpass', 250, 1800, 0.7); tone(55, now, 'sawtooth', 0.06, 1.2, { glide: 90, lp: 500 }); crackle(now + 0.2, 1.1, 22, 0.07); break;
        case 'spark': tone(1318.51 + (k % 8) * 70, now, 'triangle', 0.035, 0.08); break;
        case 'crack': noise(now, 0.12, 0.12, 'bandpass', 1400, 600, 1.2); tone(220, now, 'triangle', 0.08, 0.1, { glide: 110 }); break;
        case 'smoke': noise(now, 0.9, 0.12, 'lowpass', 1200, 200, 0.7); tone(140, now, 'sine', 0.06, 0.6, { glide: 70 }); break;
        case 'retrigger': [5, 7, 9].forEach((n, j) => mallet(PENTA[n], now + j * 0.08, 0.1)); drum(now, 330, 0.15); break;
        case 'sizzle': // a spicy banana landing
          noise(now, 0.4, 0.06, 'highpass', 4000); crackle(now, 0.35, 8, 0.05); tone(880, now, 'triangle', 0.03, 0.12, { glide: 1320 }); break;
        case 'zap': // the scientist's clone ray: a falling laser sweep over a hum
          tone(2400, now, 'sawtooth', 0.03, 0.5, { glide: 300, lp: 5000 }); tone(60, now, 'square', 0.03, 0.6, { lp: 300 }); noise(now, 0.4, 0.04, 'bandpass', 3000, 800, 4); break;
        case 'warble': // the clone ray powering up
          for (let j = 0; j < 6; j++) tone(300 + j * 140, now + j * 0.09, 'sine', 0.03, 0.12, { glide: 360 + j * 140 }); break;
        case 'drumroll': // the Music break: Onkey on the bongos (synthesized, two drums, a roll into a hit)
          for (let j = 0; j < 10; j++) drum(now + j * 0.07, j % 2 ? 300 : 220, 0.12 + j * 0.012);
          drum(now + 0.75, 180, 0.3); kick(now + 0.75, 0.3); break;
        case 'slash': // Man Strudel's knife arm
          noise(now, 0.25, 0.16, 'bandpass', 5000, 900, 3); tone(1760, now, 'sawtooth', 0.025, 0.2, { glide: 440, lp: 4000 }); break;
        case 'shape': // a wall or a square lighting up: one bright mallet note, higher for each in the spin
          mallet(PENTA[Math.min(PENTA.length - 1, 3 + k)], now, 0.09); tone(PENTA[Math.min(PENTA.length - 1, 3 + k)] * 2, now + 0.05, 'sine', 0.03, 0.2); break;
        case 'beep': // the planted spike's beep
          tone(1760, now, 'square', 0.035, 0.07, { lp: 3000 }); break;
        case 'detonate': // the third spike: a deep boom and debris
          kick(now, 0.6); tone(60, now, 'sawtooth', 0.09, 1.2, { glide: 30, lp: 400 }); noise(now, 1.3, 0.2, 'lowpass', 1800, 120, 0.7); crackle(now + 0.1, 0.8, 18, 0.08); break;
        case 'defuse': // a fizzle and two falling blips
          noise(now, 0.5, 0.06, 'bandpass', 3000, 600, 2); tone(880, now + 0.25, 'square', 0.025, 0.1, { lp: 2500 }); tone(587.33, now + 0.4, 'square', 0.025, 0.18, { lp: 2500 }); break;
        case 'golden': // the Golden Onkey: a shimmering run up and a bell
          for (let j = 0; j < 10; j++) tone(PENTA[j], now + j * 0.045, 'sine', 0.06, 0.5);
          [1567.98, 2093, 2637, 3135.96].forEach((f, j) => tone(f, now + 0.5 + j * 0.03, 'sine', 0.05, 1.4)); noise(now + 0.45, 1.2, 0.03, 'highpass', 9000); break;
        default: break;
      }
    }
    // Play a role: its clip if it has one, else the synthesized sound. The bonus music is the drum groove; both it and
    // a clip return a handle to stop.
    function play(role, k = 0, opts) {
      if (muted || document.hidden || state.view !== 'stampede') return null;
      if (!ready()) return null;
      try {
        if (WIN_ROLES.has(role)) { lastWin?.stop(0.15); lastWin = null; }
        const handle = clip(role, opts);
        if (WIN_ROLES.has(role)) lastWin = handle;
        if (handle) return handle;
        if (role === 'count_run' || role === 'encore') return null;
        if (role === 'win_epic') role = 'win_big';
        if (role === 'jackpot_big') role = 'jackpot';
        if (role === 'bonus_music') return groove();
        synth(role, k);
      } catch (_) { /* audio unavailable */ }
      return null;
    }
    const stopLoops = () => [...loops].forEach((h) => h.stop());
    return { play, stopLoops, ready };
  })();

  // ---- symbols ----------------------------------------------------------------------------------------------------
  // Drawn symbols live in one hidden SVG sprite (sprite()), used by reference in every cell.
  const sprite = () => `<svg class="st-sprite" aria-hidden="true" width="0" height="0"><defs>
    <radialGradient id="st-g-fire" cx="50%" cy="58%" r="55%"><stop offset="0" stop-color="#fffbe0"/><stop offset=".35" stop-color="#ffd23f"/><stop offset=".7" stop-color="#ff8a00"/><stop offset="1" stop-color="#d93a00"/></radialGradient>
    <radialGradient id="st-g-flame" cx="50%" cy="80%" r="70%"><stop offset="0" stop-color="#ffe066"/><stop offset=".6" stop-color="#ff7a00"/><stop offset="1" stop-color="#ff3d00" stop-opacity="0"/></radialGradient>
    <linearGradient id="st-g-val" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff6b77"/><stop offset="1" stop-color="#e02b3d"/></linearGradient>
    </defs>
    <symbol id="st-fire" viewBox="0 0 100 100"><path d="M50 4c6 14 20 18 22 34 8-6 9-14 8-20 12 14 14 30 8 44-5 13-19 32-38 32S14 76 12 60c-2-16 6-27 14-34-1 8 2 14 7 17-1-18 9-30 17-39z" fill="url(#st-g-flame)"/><circle cx="50" cy="62" r="30" fill="url(#st-g-fire)"/><circle cx="41" cy="52" r="9" fill="#fff" opacity=".55"/></symbol>
    <symbol id="st-coconut" viewBox="0 0 100 100"><path d="M62 30C58 16 64 6 71 3C76 13 73 24 67 32Z" fill="#8bc34a" stroke="#140c08" stroke-width="3.4" stroke-linejoin="round" stroke-linecap="round"/><path d="M68 32C71 20 81 13 91 13C90 24 81 32 71 36Z" fill="#6aa336" stroke="#140c08" stroke-width="3.4" stroke-linejoin="round" stroke-linecap="round"/><path d="M72 38C80 29 90 29 96 32C91 40 81 43 73 42Z" fill="#8bc34a" stroke="#140c08" stroke-width="3.4" stroke-linejoin="round" stroke-linecap="round"/><ellipse cx="40" cy="47" rx="34" ry="39" transform="rotate(-18 40 47)" fill="#7d5547" stroke="#140c08" stroke-width="3.4" stroke-linejoin="round" stroke-linecap="round"/><path d="M12 40C9 46 9 52 10 57M15 66C18 73 22 78 27 81" fill="none" stroke="#140c08" stroke-width="3.4" stroke-linejoin="round" stroke-linecap="round"/><g fill="#4a2f27" stroke="#140c08" stroke-width="3.4" stroke-linejoin="round" stroke-linecap="round"><ellipse cx="38" cy="18" rx="5.5" ry="3.6"/><ellipse cx="29" cy="28" rx="5.5" ry="3.6"/><ellipse cx="47" cy="26" rx="5.5" ry="3.6"/></g><ellipse cx="68" cy="67" rx="30" ry="31" fill="#7d5547" stroke="#140c08" stroke-width="3.4" stroke-linejoin="round" stroke-linecap="round"/><path d="M92 50C99 62 98 78 88 89C92 78 93 62 92 50Z" fill="#5a3b31"/><ellipse cx="64" cy="63" rx="28" ry="29" transform="rotate(20 64 63)" fill="#cfd8dc" stroke="#140c08" stroke-width="3.4" stroke-linejoin="round" stroke-linecap="round"/><ellipse cx="63" cy="62" rx="20" ry="22" transform="rotate(20 63 62)" fill="#fff" stroke="#140c08" stroke-width="3.4" stroke-linejoin="round" stroke-linecap="round"/><path d="M47 73C54 64 70 62 80 70C76 80 68 84 60 84C54 83 49 79 47 73Z" fill="#cfd8dc"/><path d="M47 73C54 64 70 62 80 70" fill="none" stroke="#140c08" stroke-width="3.4" stroke-linejoin="round" stroke-linecap="round"/><ellipse cx="63" cy="62" rx="20" ry="22" transform="rotate(20 63 62)" fill="none" stroke="#140c08" stroke-width="3.4" stroke-linejoin="round" stroke-linecap="round"/></symbol>
    <symbol id="st-drum" viewBox="0 0 100 100"><rect x="42" y="49" width="16" height="11" fill="#d65a4a" stroke="#2b2733" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round"/><path d="M47.5 49v11M52.5 49v11" fill="none" stroke="#2b2733" stroke-width="2"/><path d="M7 36V31C7 27 10 25 14 25H34C38 25 41 27 41 31V36Z" fill="#ffe0b0" stroke="#2b2733" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round"/><path d="M6 41H42C47 52 46 62 40 70H8C2 62 1 52 6 41Z" fill="#ff7a1f"/><path d="M17 41H31C33 52 33 62 31 70H17C15 62 15 52 17 41Z" fill="#ffb85a"/><path d="M9 44C7 52 8 60 11 67" stroke="#ff9a3d" stroke-width="2.4" fill="none" stroke-linecap="round"/><path d="M6 41H42C47 52 46 62 40 70H8C2 62 1 52 6 41Z" fill="none" stroke="#2b2733" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round"/><path d="M17 41C15 52 15 62 17 70M31 41C33 52 33 62 31 70" fill="none" stroke="#2b2733" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round"/><path d="M20 49H28" fill="none" stroke="#2b2733" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round"/><rect x="4" y="34" width="40" height="8" rx="2.5" fill="#d65a4a" stroke="#2b2733" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round"/><path d="M6 36h6v4h-6zM36 36h6v4h-6z" fill="#b03a4a"/><rect x="5" y="69" width="38" height="8" rx="2.5" fill="#d65a4a" stroke="#2b2733" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round"/><path d="M14 71h20v4h-20z" fill="#ff7a4a"/><path d="M59 36V31C59 27 62 25 66 25H86C90 25 93 27 93 31V36Z" fill="#ffe0b0" stroke="#2b2733" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round"/><path d="M58 41H94C99 52 98 62 92 70H60C54 62 53 52 58 41Z" fill="#ff7a1f"/><path d="M69 41H83C85 52 85 62 83 70H69C67 62 67 52 69 41Z" fill="#ffb85a"/><path d="M61 44C59 52 60 60 63 67" stroke="#ff9a3d" stroke-width="2.4" fill="none" stroke-linecap="round"/><path d="M58 41H94C99 52 98 62 92 70H60C54 62 53 52 58 41Z" fill="none" stroke="#2b2733" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round"/><path d="M69 41C67 52 67 62 69 70M83 41C85 52 85 62 83 70" fill="none" stroke="#2b2733" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round"/><path d="M72 49H80" fill="none" stroke="#2b2733" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round"/><rect x="56" y="34" width="40" height="8" rx="2.5" fill="#d65a4a" stroke="#2b2733" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round"/><path d="M58 36h6v4h-6zM88 36h6v4h-6z" fill="#b03a4a"/><rect x="57" y="69" width="38" height="8" rx="2.5" fill="#d65a4a" stroke="#2b2733" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round"/><path d="M66 71h20v4h-20z" fill="#ff7a4a"/></symbol>
    <symbol id="st-banana" viewBox="0 0 100 100"><path d="M84 26C82 54 62 73 33 74C28 79 32 88 40 90C72 94 94 68 93 32Z" fill="#ffc61a" stroke="#1c1300" stroke-width="3.2" stroke-linejoin="round"/><path d="M40 90C72 94 94 68 93 32C88 62 66 84 35 83Z" fill="#a87d00"/><path d="M84 26C82 54 62 73 33 74C28 79 32 88 40 90C72 94 94 68 93 32Z" fill="none" stroke="#1c1300" stroke-width="3.2" stroke-linejoin="round"/><path d="M80 21C62 33 34 36 7 29C2 34 4 43 10 46C40 59 72 50 87 28Z" fill="#ffc61a" stroke="#1c1300" stroke-width="3.2" stroke-linejoin="round"/><path d="M10 46C40 59 72 50 87 28C68 44 38 49 6 39Z" fill="#a87d00"/><path d="M14 32C38 37 60 35 76 26" stroke="#ffe680" stroke-width="2.6" fill="none" stroke-linecap="round"/><path d="M80 21C62 33 34 36 7 29C2 34 4 43 10 46C40 59 72 50 87 28Z" fill="none" stroke="#1c1300" stroke-width="3.2" stroke-linejoin="round"/><path d="M82 24C68 44 38 52 11 48C5 53 7 63 13 66C44 79 78 64 90 30Z" fill="#ffc61a" stroke="#1c1300" stroke-width="3.2" stroke-linejoin="round"/><path d="M13 66C44 79 78 64 90 30C78 55 46 68 9 58Z" fill="#a87d00"/><path d="M17 51C42 54 64 47 79 32" stroke="#ffe680" stroke-width="2.6" fill="none" stroke-linecap="round"/><path d="M82 24C68 44 38 52 11 48C5 53 7 63 13 66C44 79 78 64 90 30Z" fill="none" stroke="#1c1300" stroke-width="3.2" stroke-linejoin="round"/><path d="M77 20C78 12 87 9 93 14L92 29C87 33 79 30 77 25Z" fill="#8a4b14" stroke="#1c1300" stroke-width="3.2" stroke-linejoin="round"/><path d="M81 16C84 13 88 13 90 15" stroke="#b9722c" stroke-width="2" fill="none" stroke-linecap="round"/></symbol>
    <symbol id="st-smoke" viewBox="0 0 100 100"><g fill="#9a948c" stroke="#5c5650" stroke-width="2"><circle cx="34" cy="58" r="18"/><circle cx="54" cy="44" r="22"/><circle cx="70" cy="60" r="16"/><rect x="30" y="58" width="44" height="18" rx="9"/></g><path d="M44 30c-6-8 2-14-2-22M62 24c-4-6 2-10 0-16" stroke="#b9b3ab" stroke-width="3" fill="none" stroke-linecap="round"/></symbol>
    <symbol id="st-crown" viewBox="0 0 100 60"><path d="M6 54L12 14l22 22L50 4l16 32 22-22 6 40z" fill="#ffd23f" stroke="#a46a00" stroke-width="4" stroke-linejoin="round"/><circle cx="50" cy="40" r="6" fill="#e0242c"/><circle cx="26" cy="44" r="4.5" fill="#2f8fe8"/><circle cx="74" cy="44" r="4.5" fill="#2fbf5a"/></symbol>
    <symbol id="st-ripe" viewBox="2 10 101 74"><g transform="translate(0 -8)">
      <path d="M86 36L90 23C91 20 95 18 98 20L101 23C98 26 96 31 95 39Z" fill="#7a5320" stroke="#2b1a00" stroke-width="3" stroke-linejoin="round"/>
      <path d="M10 44C30 66 66 70 88 34L95 39C91 86 30 95 7 53C5 48 6 44 10 44Z" style="fill:var(--rb)"/>
      <path d="M95 39C91 86 30 95 7 53C33 81 80 78 91 41Z" style="fill:var(--rs)"/>
      <path d="M11 50C34 77 77 76 91 39" fill="none" style="stroke:var(--rs)" stroke-width="2.5" stroke-linecap="round"/>
      <path d="M21 53C37 66 60 68 77 50" fill="none" style="stroke:var(--rh)" stroke-width="5" stroke-linecap="round"/>
      <path d="M10 44C30 66 66 70 88 34L95 39C91 86 30 95 7 53C5 48 6 44 10 44Z" fill="none" stroke="#2b1a00" stroke-width="3.5" stroke-linejoin="round"/>
      <path d="M10 44C5 44 3 50 7 53L12 51C10 49 10 46 10 44Z" style="fill:var(--rt)" stroke="#2b1a00" stroke-width="2.5" stroke-linejoin="round"/>
      <g fill="#fff" style="opacity:var(--rspark, 0)"><path d="M28 46l2.5 6 6 2.5-6 2.5-2.5 6-2.5-6-6-2.5 6-2.5z"/><path d="M80 66l1.6 4 4 1.6-4 1.6-1.6 4-1.6-4-4-1.6 4-1.6z"/></g>
    </g></symbol>
    <symbol id="st-key" viewBox="0 0 100 100"><circle cx="32" cy="50" r="20" fill="none" stroke="#ffd23f" stroke-width="10"/><circle cx="32" cy="50" r="20" fill="none" stroke="#a46a00" stroke-width="2.5"/><path d="M50 46h40v9H82v12h-9V55h-6v9h-9V55h-8z" fill="#ffd23f" stroke="#a46a00" stroke-width="2.5" stroke-linejoin="round"/></symbol>
    <symbol id="st-valorant" viewBox="0 0 100 100"><rect x="8" y="12" width="84" height="76" rx="14" fill="#101823" stroke="#ff4655" stroke-width="3"/><polygon points="24,28 58,71 41,71 24,49" fill="url(#st-g-val)"/><polygon points="76,28 76,51 71,57 53,57" fill="url(#st-g-val)"/></symbol>
  </svg>`;
  // A ripe banana's ripeness shows its value (`v`, a multiple of the stake): green for small, yellow in the middle,
  // golden from RIPE_GOLD up. One without a value yet (landing, or covered by an event) is green.
  const RIPE_YELLOW = 2, RIPE_GOLD = 10;
  const ripeness = (v) => (!v || v < RIPE_YELLOW ? 'green' : v < RIPE_GOLD ? 'yellow' : 'gold');
  // A ripe banana's value as printed on its sticker: the credits it pays at the stake.
  const valueText = (v, st) => {
    if (!v) return '';
    const n = v * st;
    return n >= 10000 ? `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k` : Number.isInteger(n) ? String(n) : n.toFixed(n < 10 ? 2 : 1);
  };
  // Each jackpot (and the pick's smoke) has an emblem: a coin in its colour with a picture on it.
  const EMBLEM = {
    mini: { name: 'Mini', art: '<svg viewBox="0 0 100 100"><use href="#st-banana"/></svg>' },
    minor: { name: 'Minor', art: '<svg viewBox="0 0 100 100"><use href="#st-coconut"/></svg>' },
    major: { name: 'Major', art: '<img src="/assets/onkey-logo.png" alt="" draggable="false">' },
    grand: { name: 'Grand', art: '<img src="/assets/stampede/stampede-onkey.png" alt="" draggable="false"><svg class="st-crown" viewBox="0 0 100 60"><use href="#st-crown"/></svg>' },
    smoke: { name: 'Smoke', art: '<svg viewBox="0 0 100 100"><use href="#st-smoke"/></svg>' },
    vault: { name: 'Jackpot', art: '<svg viewBox="0 0 100 100"><use href="#st-key"/></svg>' },
  };
  const emblem = (kind, named = true) => `<span class="st-emb st-emb-${kind}" role="img" aria-label="${EMBLEM[kind].name}"><span class="st-emb-art">${EMBLEM[kind].art}</span>${named ? `<b>${EMBLEM[kind].name}</b>` : ''}</span>`;
  // One symbol's face. `v` is a ripe banana's value, `m` a free-spin wild's multiplier.
  function face(i, v, m) {
    const s = SYM(i), k = s.key;
    if (s.tier === 'low') return `<span class="st-low st-l-${k}">${esc(s.name)}</span>`;
    if (k === 'greg') return '<img class="st-img st-greg" src="/assets/greg-logo.png" alt="" draggable="false">';
    if (k === 'onkey') {
      return data.october
        ? '<img class="st-img st-pumpkin" src="/assets/stampede/halloween-onkey.png" alt="" draggable="false">'
        : '<img class="st-img st-onkey" src="/assets/onkey-logo.png" alt="" draggable="false">';
    }
    // The Golden Onkey, the secret symbol: only ever drawn where a spin landed one.
    if (k === 'golden') return '<img class="st-img st-golden" src="/assets/onkey-logo.png" alt="" draggable="false"><b class="st-tag st-tag-gold">WILD ×3</b>';
    // The spicy banana bunch: a banana bunch on fire, wild, with its pepper (`m`) once it has landed.
    if (k === 'spicy') return `<svg class="st-svg st-spicy-svg" viewBox="0 0 100 100"><use href="#st-banana"/></svg><span class="st-chili" aria-hidden="true">🌶️</span><b class="st-tag st-tag-hot">WILD</b>${m ? `<b class="st-mult">×${m}</b>` : ''}`;
    if (k === 'wild') return `<img class="st-img st-wild-img" src="/assets/stampede/wild.png" alt="" draggable="false"><b class="st-tag">WILD</b>${m ? `<b class="st-mult">×${m}</b>` : ''}`;
    if (k === 'spike') return '<img class="st-img st-spike-img" src="/assets/stampede/spike.png" alt="" draggable="false">';
    if (k === 'fire') return `<svg class="st-svg st-ripe st-ripe-${ripeness(v)}" viewBox="0 0 101 74"><use href="#st-ripe"/></svg>${v ? `<b class="st-val st-sticker">${valueText(v, stake)}</b>` : ''}`;
    return `<svg class="st-svg" viewBox="0 0 100 100"><use href="#st-${k}"/></svg>`;
  }
  const cellHtml = (i, v, m, extra = '') => `<div class="st-cell st-t-${SYM(i).tier} st-k-${SYM(i).key} ${extra}" role="img" aria-label="${esc(SYM(i).name)}${v ? ` ${valueText(v, stake)}` : ''}">${face(i, v, m)}</div>`;
  const keyOf = (c, r) => `${c}-${r}`;
  const valueMap = (list) => Object.fromEntries((list || []).map(([c, r, v]) => [keyOf(c, r), v]));

  // ---- the page ---------------------------------------------------------------------------------------------------
  function view() {
    syncOwner();
    if (!data || (data.me?.name || null) !== owner) {
      load().then(() => { if (state.view === 'stampede') draw(); }).catch(() => {
        const el = $('#st-loading'); if (el) el.textContent = 'Could not load Onkey Stampede. Open this tab again to retry.';
      });
      return '<section class="card" id="st-loading">Loading Onkey Stampede…</section>';
    }
    const insufficient = state.me && displayBalance() < stake && !busy;
    return `${sprite()}<div class="st-layout"><div class="st-main"><section class="st-cabinet${data.october ? ' st-october' : ''}${busy ? ' st-busy' : ''}" id="st-cabinet">
        ${jackpotBar()}${meterBar()}
        <div class="st-title"><img class="st-title-onkey" src="/assets/stampede/stampede-onkey.png" alt="" draggable="false"><h2>Onkey <span>Stampede</span></h2><span class="st-ways">${data.october ? 'Halloween · ' : ''}1,024 ways</span>
          <button class="st-sound" id="st-sound" aria-pressed="${!muted}" aria-label="${muted ? 'Turn sound on' : 'Mute sound'}" title="${muted ? 'Turn sound on' : 'Mute sound'}">${window.speakerIcon(muted, 20)}</button></div>
        <div class="st-stage"><div class="st-banner-slot" id="st-banner"></div><div class="st-counter hidden" id="st-counter"></div>
          <div class="st-window" id="st-window"><div class="st-reels" id="st-reels">${reelsHtml()}</div><div class="st-fx" id="st-fx"></div></div>
          <div class="st-winline" id="st-winline" role="status" aria-live="polite">${winLine()}</div></div>
        <div class="st-deck">
          <div class="st-well st-stakes" role="group" aria-label="Credits per spin">${M().stakes.map((s) => `<button type="button" data-st-stake="${s}" aria-pressed="${s === stake}" ${busy || pending ? 'disabled' : ''}><small>Bet</small><b>${s}</b></button>`).join('')}</div>
          <div class="st-well st-readout">${readout()}</div>
          <div class="st-well st-opts"><button type="button" class="st-opt${auto ? ' on' : ''}" id="st-auto" aria-pressed="${!!auto}" ${!state.me ? 'disabled' : ''}>Auto<b>${auto ? (busy ? autoLeft : auto) : 'off'}</b></button><button type="button" class="st-opt${nonstop ? ' on' : ''}" id="st-nonstop" aria-pressed="${nonstop}" title="Auto keeps spinning through bonuses and big wins (it still stops when your credits run short)">Nonstop<b>${nonstop ? 'on' : 'off'}</b></button><button type="button" class="st-opt${turbo ? ' on' : ''}" id="st-turbo" aria-pressed="${turbo}">Turbo<b>${turbo ? 'on' : 'off'}</b></button></div>
          ${state.me && data.daily?.left ? `<div class="st-well st-daily-well"><button type="button" class="st-daily" id="st-daily" ${busy || pending ? 'disabled' : ''} title="Free: the house pays the ${data.daily.stake}-credit bet"><small>Daily spin</small><b>${data.daily.left} left</b><small>free at ${data.daily.stake}</small></button></div>` : ''}
          <div class="st-well st-spin-well">${state.me
            ? `<button class="st-spin" id="st-spin" aria-keyshortcuts="Space" ${(insufficient && !pending) ? 'disabled' : ''} aria-label="${busy ? 'Skip ahead' : pending ? 'Check last spin' : `Spin for ${stake} credits`}"><span>${busy ? 'Skip' : pending ? 'Check' : 'Spin'}</span>${pending && !busy ? '<small>last spin</small>' : ''}</button>`
            : '<button class="st-spin" data-signin><span>Sign in</span><small>to spin</small></button>'}</div>
        </div>
        <p class="st-error" role="alert">${esc(error || (insufficient && !pending ? 'Not enough credits. Choose a smaller stake.' : ''))}</p>
        ${data.forces?.length ? `<div class="st-demo"><span>Demo: spin for</span>${data.forces.map((f) => `<button type="button" class="btn small ghost" data-st-force="${f}" ${busy || !state.me ? 'disabled' : ''}>${esc(f.replace('_', ' '))}</button>`).join('')}</div>` : ''}
      </section><div class="st-lower">${feedCard()}${history()}${season()}${bigWins()}</div></div>${side()}</div>`;
  }
  function reelsHtml() {
    return shown.grid.map((col, c) => `<div class="st-reel" data-reel="${c}"><div class="st-strip">${col.map((s, r) => cellHtml(s, shown.values[keyOf(c, r)], shown.wilds[keyOf(c, r)])).join('')}</div></div>`).join('');
  }
  const readout = () => {
    const cell = (label, value, id) => `<span><small>${label}</small><b${id ? ` id="${id}"` : ''}>${value}</b></span>`;
    return cell('Credits', state.me ? money(displayBalance()) : '–', 'st-credits') + cell('Bet', money(stake)) + cell('Win', last && !busy ? money(paidOf(last)) : '0', 'st-win');
  };
  const winLine = () => {
    if (busy) return '<b>Good luck</b>';
    if (encore > Date.now() && last) return `<b class="st-won">Win! +${money(paidOf(last) - last.stake)}</b><span>Onkey is singing for you.</span>`;
    if (!last) return '<b>Three or more of a kind on adjacent reels from the left, or a shape anywhere</b><span>Space spins. Press it again to skip ahead.</span>';
    return resultLine(last);
  };
  // What a spin paid in all: the machine's payout, plus the vault's JACKPOT (paid by the house) when it opened.
  const paidOf = (s) => s.payout + (s.result.heist?.jackpot || 0);
  // What a spin came to, said plainly: a win only when it paid more than the bet.
  function resultLine(s) {
    const r = s.result, bits = [];
    if (r.heist?.jackpot) bits.push(`the vault's JACKPOT ${money(r.heist.jackpot)}`);
    else if (r.heist) bits.push(`vault heist ${money(r.heist.cash ?? r.heist.mult * s.stake)}`);
    if (r.daily) bits.push('a free daily spin');
    if (r.free_spins) bits.push(`free spins ${money(r.free_spins.mult * s.stake)}`);
    if (r.hold) bits.push(`hold and spin ${money(r.hold.cash * s.stake)}`);
    (r.jackpots || []).forEach((j) => bits.push(`${j.name} jackpot ${money(j.amount)}`));
    if (r.pick && !r.jackpots?.length) bits.push('the pick went up in smoke');
    const extra = bits.length ? `<span>${esc(bits.join(' · '))}</span>` : '';
    if (paidOf(s) > s.stake) return `<b class="st-won">Win! +${money(paidOf(s) - s.stake)}</b>${extra || `<span>Paid ${money(s.payout)} on a ${money(s.stake)} bet</span>`}`;
    if (s.payout > 0) return `<b class="st-back">${esc(smallWin(r))} +${money(s.payout)}</b>${extra || `<span>${money(s.payout)} of your ${money(s.stake)} back</span>`}`;
    return `<b class="st-none">No win</b>${extra || '<span>The next one, surely.</span>'}`;
  }
  // What paid on a spin that came to less than the bet: its shapes, or its ways wins.
  function smallWin(r) {
    const sh = r.shapes || [];
    if (sh.length > 1) return `${sh.length} shapes!`;
    if (sh.length) return `${shapeName(sh[0])}!`;
    return r.wins?.length ? `${SYM(r.wins[0].symbol).name}!` : 'Back';
  }
  // The win feed: notable spins (big wins, bonuses, heists, jackpots, achievements), everyone's or your own.
  const ago = (ts) => { const m = Math.round((Date.now() / 1000 - ts) / 60); return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`; };
  const feedWhat = (f) => [f.vault > 0 && 'JACKPOT', ...featureTags(f).filter((t) => t !== 'JACKPOT'), f.shape, ...(f.unlocked || []).map((i) => `Unlocked ${achName(i)}`), f.daily && 'Daily spin'].filter(Boolean);
  const feedPaid = (f) => f.payout + (f.vault || 0);
  const feedRows = (list) => (list || []).map((f) => `<li class="${f.vault > 0 ? 'st-feed-vault' : ''}"><span class="st-feed-who"><span>${plain(f.bettor)}</span><small class="muted">${ago(f.created_ts)}</small></span>
      <span class="st-feed-what">${feedWhat(f).map((t) => `<span class="st-chip">${esc(t)}</span>`).join('') || `<span class="muted small">${f.multiplier.toFixed(1)}×</span>`}</span><b class="${feedPaid(f) > f.stake ? 'up' : ''}">${money(feedPaid(f))}</b></li>`).join('')
    || '<li class="muted small">Nothing yet. Big wins, bonuses and heists show up here.</li>';
  function feedCard() {
    const tab = (k, label) => `<button type="button" class="mode-btn ${feedTab === k ? 'on' : ''}" data-st-feed="${k}" aria-pressed="${feedTab === k}">${label}</button>`;
    return `<section class="card st-feed-card"><div class="section-head"><h2>Win feed</h2><div class="slip-mode" role="group" aria-label="Whose wins">${tab('all', 'Everyone')}${state.me ? tab('mine', 'Yours') : ''}</div></div>
      <ul class="st-feed" id="st-feed-list">${feedRows(feedTab === 'mine' ? data.my_feed : data.feed)}</ul></section>`;
  }
  // Someone else's notable spin, while you're here: a line across the cabinet for a few seconds.
  function ticker(f) {
    const slot = $('#st-ticker');
    if (!slot) return;
    const what = feedWhat(f)[0] || `${f.multiplier.toFixed(0)}× win`;
    slot.insertAdjacentHTML('beforeend', `<div class="st-tick${f.vault > 0 ? ' st-tick-vault' : ''}">🔔 ${plain(f.bettor)} <span>${esc(what)}</span> <b>+${money(feedPaid(f))}</b></div>`);
    const el = slot.lastElementChild;
    setTimeout(() => el?.classList.add('out'), 6000);
    setTimeout(() => el?.remove(), 6600);
  }
  const achName = (id) => data.achievements?.find((a) => a.id === id)?.name || id;
  // Achievements: earned-only looks for your name, unlocked by the machine's rarest moments.
  function achievementsCard() {
    const list = data.achievements;
    if (!list) return '';
    const icon = (a) => (a.look.emoji ? esc(a.look.emoji) : '🏷️');
    const have = list.filter((a) => a.have).length;
    return `<div class="st-feature st-ach"><span class="st-shape-ico st-ach-ico" aria-hidden="true">🏆</span><div><b>Achievements <span class="muted small">${have} / ${list.length}</span></b><span>Earned here, never sold: each puts a badge or title next to your name across the site (wear it from Onkey's Shop).</span>
      <ul class="st-ach-list">${list.map((a) => `<li class="${a.have ? 'have' : ''}"><span class="st-ach-icon">${icon(a)}</span><span><b>${esc(a.name)}</b><small>${esc(a.earn)}</small></span>${a.have ? '<em>✓</em>' : ''}</li>`).join('')}</ul></div></div>`;
  }
  // The four jackpots across the top, in credits at the stake chosen.
  // The JACKPOT across the very top: the house's progressive jackpot (the daily wheel's), which the vault door pays.
  const vaultText = () => (vaultShown ?? 0).toLocaleString();
  function vaultBar() {
    if (data.jackpot == null) return '';
    return `<div class="st-vault-pot" id="st-vault-pot">${emblem('vault', false)}<b class="st-vault-word">JACKPOT</b><b class="st-vault-val" data-vault>${vaultText()}</b><small>Crack the vault in a heist to take it all · the daily wheel's jackpot too</small></div>`;
  }
  function jackpotBar() {
    return `${vaultBar()}<div class="st-ticker" id="st-ticker" aria-live="polite"></div><div class="st-jackpots" id="st-jackpots">${['grand', 'major', 'minor', 'mini'].map((k) => {
      const p = data.pots.find((x) => x.key === k);
      return `<div class="st-pot st-pot-${k}">${emblem(k, false)}<small>${esc(p.name)}</small><b data-pot="${k}">${potText(k)}</b></div>`;
    }).join('')}</div>`;
  }
  // Your banana basket, under the jackpots: every ripe banana you land fills it; full, it opens the jackpot pick.
  function meterBar() {
    if (!state.me || !data.meter) return '<div class="st-meter off"><span class="st-meter-label">Banana basket</span><span class="st-meter-hint">Sign in to fill your own basket, pick for jackpots and collect vault keys</span></div>';
    const full = data.meter.full, pct = Math.min(100, (meterShown / full) * 100);
    return `<div class="st-meters"><div class="st-meter${pct >= 85 ? ' hot' : ''}" id="st-meter"><span class="st-meter-label">🧺 Banana basket</span>
      <div class="st-meter-track"><div class="st-meter-fill" id="st-meter-fill" style="width:${pct}%"></div></div>
      <b class="st-meter-count" id="st-meter-count">${meterText(meterShown, full)}</b><span class="st-meter-hint" id="st-meter-hint">${meterHint()}</span></div>${keysBox()}${progressBox()}</div>`;
  }
  // Vault keys: a square whose sides light up one per key (a Golden Onkey drops one); all four start the Vault Heist.
  function keysBox() {
    const full = data.keys?.full || M().vault.keys;
    const sides = [[4, 4, 36, 4], [36, 4, 36, 36], [36, 36, 4, 36], [4, 36, 4, 4]];
    return `<div class="st-keys${keysShown >= full ? ' full' : ''}" id="st-keys" title="Vault keys: every Golden Onkey drops one; ${full} start the Vault Heist">
      <svg viewBox="0 0 40 40" width="40" height="40" aria-hidden="true">${sides.map(([a, b, c, d], i) => `<line x1="${a}" y1="${b}" x2="${c}" y2="${d}" class="${i < keysShown ? 'on' : ''}"/>`).join('')}
        <use href="#st-key" x="10" y="10" width="20" height="20"/></svg>
      <span><b>Vault keys</b><small id="st-keys-count">${Math.min(keysShown, full)} / ${full}</small></span></div>`;
  }
  function paintKeys(n) {
    keysShown = n;
    const box = $('#st-keys'), full = data.keys?.full || M().vault.keys;
    if (!box) return;
    box.querySelectorAll('line').forEach((l, i) => l.classList.toggle('on', i < n));
    box.classList.toggle('full', n >= full);
    const count = $('#st-keys-count');
    if (count) count.textContent = `${Math.min(n, full)} / ${full}`;
  }
  // The Golden Onkey's key flies from his cell into the key square, and its next side lights up.
  function flyKey(cell, n) {
    const box = $('#st-keys');
    const from = cell?.getBoundingClientRect(), to = box?.getBoundingClientRect();
    if (!from || !to) { paintKeys(n); return sleep(0); }
    const key = document.createElement('span');
    key.className = 'st-flying-key';
    key.innerHTML = '<svg viewBox="0 0 100 100"><use href="#st-key"/></svg>';
    key.style.left = `${from.left + from.width / 2}px`; key.style.top = `${from.top + from.height / 2}px`;
    document.body.appendChild(key);
    const dx = to.left + 20 - (from.left + from.width / 2), dy = to.top + 20 - (from.top + from.height / 2);
    SFX.play('scatter', 1);
    return new Promise((resolve) => {
      key.animate([{ transform: 'translate(-50%,-50%) scale(1.6) rotate(0)' }, { transform: `translate(calc(-50% + ${dx * 0.4}px), calc(-50% + ${dy - 90}px)) scale(1.3) rotate(200deg)`, offset: 0.5 },
        { transform: `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px)) scale(.6) rotate(360deg)` }], { duration: T(900), easing: 'ease-in-out' })
        .onfinish = () => { key.remove(); paintKeys(n); SFX.play('retrigger'); $('#st-keys')?.classList.add('st-pop'); setTimeout(() => $('#st-keys')?.classList.remove('st-pop'), 500); resolve(); };
    });
  }
  const meterText = (n, full) => `${Math.floor(Math.min(n, full)).toLocaleString()} / ${full.toLocaleString()}`;
  // How fast your bet fills it: each ripe banana adds the bet, so at this bet the pick comes about every N spins.
  function meterHint() {
    return `Each ripe banana adds your bet (+${money(stake)}). At this bet, a pick about every ${Math.round(1 / (M().pick_per_credit * stake)).toLocaleString()} spins.`;
  }
  // The two quiet meters: the scientist's evidence board (a photo per clone ray) and Man Strudel's friendship (a heart
  // per slice). Each shows only once it has started filling, as a row of pips.
  function progressBox() {
    const pr = data.progress;
    if (!pr) return '';
    const pips = (n, full, on, off) => Array.from({ length: full }, (_, i) => `<i class="${i < n ? 'on' : ''}">${i < n ? on : off}</i>`).join('');
    const lab = pr.lab.length, friend = pr.friend.length;
    return (lab ? `<div class="st-prog st-prog-lab" id="st-prog-lab" title="The scientist's evidence board: every clone ray pins a photo; ${pr.lab_full} start the Big Experiment"><span>🧪</span><span class="st-pips">${pips(lab, pr.lab_full, '📷', '·')}</span></div>` : '')
      + (friend ? `<div class="st-prog st-prog-friend" id="st-prog-friend" title="Man Strudel's friendship: every slice adds a heart; at ${pr.friend_full} he switches sides for ${M().defect_spins} spins"><span>🤝</span><span class="st-pips">${pips(friend, pr.friend_full, '❤️', '·')}</span></div>` : '');
  }
  // After a spin: the meters drawn again from where they ended (a full one empties as its bonus plays).
  function paintProgress(prog) {
    if (!prog) return;
    data.progress = { ...(data.progress || { lab_full: M().lab_full, friend_full: M().friend_full }), lab: Array(prog.lab).fill(0), friend: Array(prog.friend).fill(0) };
    const wrap = $('.st-meters');
    if (!wrap) return;
    wrap.querySelectorAll('.st-prog').forEach((el) => el.remove());
    wrap.insertAdjacentHTML('beforeend', progressBox());
  }
  function paintMeter(n) {
    meterShown = n;
    const full = data.meter?.full || M().meter_full, fill = $('#st-meter-fill'), count = $('#st-meter-count');
    if (fill) fill.style.width = `${Math.min(100, (n / full) * 100)}%`;
    if (count) count.textContent = meterText(n, full);
    $('#st-meter')?.classList.toggle('hot', n / full >= 0.85);
    $('#st-meter')?.classList.toggle('full', n >= full);
  }
  const potText = (k) => (pots?.[k] ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  function paintPots() {
    $$('[data-pot]').forEach((el) => { el.textContent = potText(el.dataset.pot); });
    $$('[data-vault]').forEach((el) => { el.textContent = vaultText(); });
  }
  // The jackpots count up to the server's figures, a little at a time, so the squad's spins show as they land.
  function startPots() {
    clearInterval(potTimer);
    potTimer = setInterval(async () => {
      if (state.view !== 'stampede') { clearInterval(potTimer); potTimer = null; return; }
      if (busy || document.hidden) return;
      try {
        const got = await api('/api/stampede/pots');
        if (busy) return;
        potTarget = Object.fromEntries(got.pots.map((p) => [p.key, p.size]));
        data.pots = got.pots; data.jackpot_log = got.jackpot_log;
        if (got.jackpot != null) { data.jackpot = got.jackpot; vaultShown = got.jackpot; paintPots(); }
        if (got.feed) {
          const fresh = got.feed.filter((f) => f.id > feedSeen && f.bettor !== state.me?.name).reverse();
          feedSeen = Math.max(feedSeen, ...got.feed.map((f) => f.id));
          data.feed = got.feed;
          const box = $('#st-feed-list');
          if (box && feedTab === 'all') box.innerHTML = feedRows(data.feed);
          for (const f of fresh.slice(-3)) ticker(f);
        }
      } catch (_) { /* try again next time */ }
    }, 5000);
    if (!potTick) {
      potTick = setInterval(() => {
        if (!pots || !potTarget || state.view !== 'stampede') return;
        let moved = false;
        for (const k of Object.keys(potTarget)) {
          const gap = potTarget[k] - pots[k];
          if (Math.abs(gap) < 1e-6) continue;
          // Count up gently; a reset (a jackpot won elsewhere) drops straight down.
          pots[k] = gap < 0 ? potTarget[k] : pots[k] + Math.max(gap * 0.12, Math.min(gap, 0.0004));
          moved = true;
        }
        if (moved) paintPots();
      }, 120);
    }
  }

  function side() {
    const st = stake, pays = M().pays;
    const paying = M().symbols.map((s, i) => ({ s, i })).filter(({ s }) => pays[String(idx(s.key))]).reverse();
    const fmtPay = (m) => { const n = m * st; return Number.isInteger(n) ? String(n) : n.toFixed(n < 1 ? 2 : 1); };
    const rows = paying.map(({ i }) => `<tr><th scope="row"><span class="st-mini">${cellHtml(i)}</span><span class="sr-only">${esc(SYM(i).name)}</span></th>${pays[String(i)].map((m) => `<td class="num">${fmtPay(m)}</td>`).join('')}</tr>`).join('');
    const sc = M().scatter_pays, fs = M().fs_award;
    const chance = (p) => `1 in ${Math.round(1 / p).toLocaleString()}`;
    return `<aside class="st-side card">
      <h2>Pays at ${money(st)} a spin</h2>
      <table class="st-pays"><thead><tr><th>Symbol</th><th class="num">×3</th><th class="num">×4</th><th class="num">×5</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="muted small">Per way. Ways multiply: two on reel 1 and two on reel 2 make four ways.</p>
      <div class="st-feature"><span class="st-mini">${cellHtml(idx('wild'))}</span><div><b>Bongo Onkey is wild</b><span>Stands in for every symbol above, on reels 2 to 5.</span></div></div>
      ${shapesCard(st)}
      ${achievementsCard()}
      <div class="st-feature"><span class="st-mini">${cellHtml(idx('spike'))}</span><div><b>2 spikes: spike planted</b><span>Sometimes the spike plants: the reels without one spin again, looking for the third. ${chance(M().plant_chance)} spins.</span></div></div>
      <div class="st-feature"><span class="st-mini">${cellHtml(idx('spike'))}</span><div><b>3+ spikes: free spins</b><span>${Object.entries(fs).map(([n, s]) => `${n} give ${s}`).join(', ')}, and pay ${Object.entries(sc).map(([n, m]) => `${money(m * st)}`).join(' / ')}. Wilds carry ×2 or ×3 and multiply each other. ${chance(M().fs_chance)} spins.</span></div></div>
      <div class="st-feature"><span class="st-mini">${cellHtml(idx('fire'), 10)}</span><div><b>6+ ripe bananas: hold and spin</b><span>Each carries credits on its sticker, and the riper it looks the more it pays (green, yellow, golden). Six or more stick, and you get ${M().hs_respins} respins; every new one resets them to ${M().hs_respins}. Fill all 20 for a ${M().full_grid_bonus}× bonus. ${chance(M().hs_chance)} spins.</span></div></div>
      <div class="st-feature"><span class="st-mini st-mini-emb">${emblem('grand', false)}</span><div><b>Banana basket: pick for a jackpot</b><span>Every ripe banana you land adds your bet to your own basket, so bigger bets fill it faster. At ${M().meter_full.toLocaleString()} you peel bananas: three of one jackpot wins it, three smokes and it's gone. At ${money(st)} a spin, about ${chance(M().pick_per_credit * st)} spins.</span></div></div>
      <div class="st-feature"><span class="st-mini">${cellHtml(idx('spicy'), null, 2)}</span><div><b>Spicy bananas: wild and hot</b><span>Wild on reels 3 to 5. When one lands on a spin that wins, Onkey eats it and every win that spin is multiplied by its pepper (×${M().spicy_mults.join(' or ×')}); two or more multiply together. ${chance(M().spicy_chance)} spins.</span></div></div>
      ${how('The jackpots and the events', `<p><b>Jackpot pick:</b> ${M().pick_kinds.map((k) => `${EMBLEM[k].name} ${Math.round(M().pick_chances[k] * 1000) / 10}%`).join(', ')}. The jackpots are shared, the same credits for everyone, and grow with every spin anyone makes (by a share of the bet); one goes back to its starting size when it's won. A bigger bet doesn't make them bigger, it gets you to the pick sooner: the chance per credit you bet is the same at every stake.</p>
        <p><b>Stampede:</b> Onkey charges across and leaves wilds on reels 2 to 5, always enough for a win. <b>Banana rain:</b> bananas fall and land as ripe bananas. <b>The clone ray:</b> the scientist copies reel 1 onto reels 2 and 3, bananas and spikes included. <b>Banana split:</b> Onkey splits a symbol in two, and it counts twice in the ways. <b>Music break:</b> Onkey drops a pair of bongo drums on a reel, and every reel showing a drum doubles every win on the spin. <b>Strudel's slice:</b> Man Strudel slashes a line across reels 2 to 4 and every cell he cuts turns wild, so everything on reel 1 wins. One of them about every ${Math.round(1 / M().event_chance)} spins.</p>
        <p><b>The evidence board and Man Strudel:</b> every clone ray pins a photo to the scientist's board; at ${M().lab_full} he runs the Big Experiment, one spin with reel 1 copied onto three reels. Every slice adds to Man Strudel's friendship; at ${M().friend_full} he switches sides for ${M().defect_spins} spins, slashing a line of wilds on each. Both pay their ways and shapes at the average bet of the spins that filled them, and show as pips beside the basket once they've started.</p>
        <p><b>The Vault Heist:</b> now and then Onkey turns up holding a key (about 1 spin in ${Math.round(1 / M().vault.chance).toLocaleString()}). He cracks the vault's ${M().vault.locks} locks one at a time while the scientist sends Man Strudel after it; the first lock that holds ends it, paying ${M().vault.pays.map((m, i) => `${m}× for ${i} open`).join(', ')}. Past every lock, the vault door opens ${doorOdds(stake)} at your bet (bigger bets, better odds) and pays the whole JACKPOT.</p>
        <p>A spin is a win when it pays more than the bet; smaller payouts give part of the bet back. Returns ${M().rtp}% of stakes over time, jackpots included. The edge feeds the daily wheel's jackpot; nothing here comes out of it.</p>`)}
      ${data.jackpot_log.length ? `<h3>Latest jackpots</h3><ul class="st-jplog">${data.jackpot_log.slice(0, 5).map((j) => `<li><span class="st-jp-name st-pot-${j.key}">${esc(j.key[0].toUpperCase() + j.key.slice(1))}</span>${plain(j.bettor)}<b>${money(j.amount)}</b></li>`).join('')}</ul>` : ''}
    </aside>`;
  }
  // Shapes: each one drawn as a little grid, and what it pays at the stake chosen, by the symbol's tier.
  function shapeIcon(form) {
    const cells = form.flat(), w = Math.max(...cells.map(([c]) => c)) + 1, h = Math.max(...cells.map(([, r]) => r)) + 1, u = 9;
    const on = new Set(cells.map(([c, r]) => `${c}-${r}`)), dots = [];
    for (let c = 0; c < w; c++) for (let r = 0; r < h; r++) dots.push(`<circle cx="${c * u + 5}" cy="${r * u + 5}" r="${on.has(`${c}-${r}`) ? 3.4 : 1.6}"${on.has(`${c}-${r}`) ? ' class="on"' : ''}/>`);
    const lines = form.map((st) => `<polyline points="${st.map(([c, r]) => `${c * u + 5},${r * u + 5}`).join(' ')}"/>`).join('');
    return `<svg class="st-shape-icon" viewBox="0 0 ${w * u + 1} ${h * u + 1}" width="${w * u + 1}" height="${h * u + 1}" aria-hidden="true">${lines}${dots.join('')}</svg>`;
  }
  function shapesCard(st) {
    const TIERS_SHOWN = [['low', '9–A'], ['mid', 'Coco, drum'], ['high', 'Banana, Val, Greg'], ['top', 'Onkey']];
    const rows = Object.values(M().shapes).map((x) => `<tr><th scope="row"><span class="st-shape-cell">${shapeIcon(x.forms[0])}${esc(x.name)}</span></th>${TIERS_SHOWN.map(([t]) => `<td class="num">${money(M().shape_base[t] * x.factor * st)}</td>`).join('')}</tr>`).join('');
    return `<div class="st-feature st-shapes-feature"><span class="st-shape-ico" aria-hidden="true">${shapeIcon([[[0, 0], [1, 1], [2, 0]]])}</span><div><b>Shapes pay too</b><span>Anywhere on the reels, on top of the ways: two or more of one symbol (wilds fill in the rest) in one of these shapes. A bigger shape pays instead of the smaller ones inside it, and shapes can share cells. Ripe bananas make shapes too and pay ${Math.round(M().fire_share * 100)}% of the credits on them. A spicy banana multiplies shapes, and so does the biggest multiplying wild in one.</span>
      <table class="st-pays st-shape-pays"><thead><tr><th></th>${TIERS_SHOWN.map(([, label]) => `<th class="num">${label}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div></div>`;
  }
  const doorOdds = (st) => `1 time in ${Math.round(1 / M().vault.door[String(st)]).toLocaleString()}`;
  const plain = (name) => plainName(name);
  const featureTags = (s) => [s.free_spins && 'Free spins', s.hold && 'Hold & spin', s.plant && 'Spike planted', s.event === 'stampede' && 'Stampede', s.event === 'rain' && 'Banana rain',
    s.event === 'clone' && 'Clone ray', s.event === 'split' && 'Banana split', s.event === 'music' && 'Music break', s.experiment && 'Big Experiment', s.defect && 'Strudel on your side', s.vault > 0 && 'JACKPOT', s.heist != null && !(s.vault > 0) && 'Vault heist', s.event === 'greg' && "Greg's takeover", s.event === 'slice' && "Strudel's slice", s.heat && `Spicy ×${s.heat}`, s.inferno && `Inferno ×${s.inferno}`, s.pick && !(s.jackpots || []).length && 'Jackpot pick', ...(s.jackpots || []).map((k) => `${k[0].toUpperCase()}${k.slice(1)} jackpot`)].filter(Boolean);
  function history() {
    const rows = (data.history || []).map((s) => `<tr><td>${new Date(s.created_ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</td><td class="num">${money(s.stake)}</td><td>${featureTags(s).map((t) => `<span class="st-chip">${esc(t)}</span>`).join('') || '<span class="muted">–</span>'}</td><td class="num ${s.net > 0 ? 'up' : ''}">${s.payout ? money(s.payout) : '–'}</td></tr>`).join('');
    return `<section class="card"><h2>Your recent spins</h2>${rows ? `<table class="st-history"><thead><tr><th>Time</th><th class="num">Bet</th><th>Features</th><th class="num">Won</th></tr></thead><tbody>${rows}</tbody></table>` : `<p class="muted">${state.me ? 'No spins yet this season.' : '<a href="#" data-signin>Sign in</a> to spin.'}</p>`}</section>`;
  }
  function season() {
    const me = data.me;
    if (!me) return '<section class="card"><h2>Your season</h2><p class="muted">Sign in to see yours.</p></section>';
    const tile = (label, value, sub = '') => `<div class="tile"><div class="tile-label">${label}</div><div class="tile-value">${value}</div>${sub ? `<div class="tile-sub">${sub}</div>` : ''}</div>`;
    return `<section class="card"><h2>Your season</h2><div class="st-tiles">${tile('Spins', me.spins.toLocaleString())}${tile('Net', `<span class="${me.net >= 0 ? 'up' : 'down'}">${fmt.signed ? fmt.signed(me.net, 0) : me.net}</span>`)}${tile('Bonuses', me.free_spins + me.holds + me.picks, `${me.free_spins} free spins · ${me.holds} hold and spin · ${me.picks} picks`)}${tile('Best win', me.best ? money(me.best.payout) : '–', me.best ? `${money(me.best.stake)} bet · ${me.best.multiplier.toFixed(1)}×` : '')}</div></section>`;
  }
  function bigWins() {
    const rows = (data.big_wins || []).map((s) => `<li>${plain(s.bettor)}<span class="muted small">${featureTags(s).join(' · ') || `${s.multiplier.toFixed(1)}×`}</span><b>${money(s.payout)}</b></li>`).join('');
    return `<section class="card"><h2>Biggest wins this season</h2>${rows ? `<ol class="st-big">${rows}</ol>` : '<p class="muted">Nobody yet. Be the first.</p>'}</section>`;
  }

  // ---- reels in motion ----------------------------------------------------------------------------------------------
  // Each reel's strip during a spin is [the four it will land on, the strip's next cells, the four it shows now],
  // moved from showing the bottom four to showing the top four. Its speed rises quickly, holds, then eases out; the
  // landing bounces a little. Each reel stops `gap` after the last; a tease (two spikes, or four fireballs, already
  // showing) holds the next reels longer and lights them.
  const easeAt = (() => {
    const a = 0.07, d = 0.3, total = a / 2 + (1 - d - a) + d / 3;
    const S = (u) => {
      if (u <= a) return (u * u) / (2 * a);
      if (u <= 1 - d) return a / 2 + (u - a);
      const w = (u - (1 - d)) / d;
      return a / 2 + (1 - d - a) + d * (w - w * w + (w * w * w) / 3);
    };
    const f = (u) => Math.min(1, S(Math.max(0, u)) / total);
    f.total = total;
    return f;
  })();
  // `only` (the spike plant) moves just those reels, every one of them teasing.
  function roll({ strips, stops, grid, values = {}, wilds = {}, extra = 0, lead = 900, gap = 230, only = null }) {
    const reels = $$('.st-reel'), H = reels[0].clientHeight / 4;
    const now = performance.now(), plan = [];
    let fires = 0, spikes = 0, at = now + T(lead + extra), first = true;
    grid.forEach((col, c) => {
      if (only && !only.includes(c)) { plan.push(null); return; }
      // A tease: the spikes or fireballs already showing could make a bonus with this reel.
      const tease = only ? true : c >= 2 && (spikes >= 2 || fires >= 4);
      if (!first) at += T(gap) + (tease ? T(only ? 800 : 1500) : 0);
      first = false;
      plan.push({ end: at, tease });
      spikes += col.filter((s) => SYM(s).key === 'spike').length;
      fires += col.filter((s) => SYM(s).key === 'fire').length;
    });
    const prevEnd = (c) => { for (let k = c - 1; k >= 0; k--) if (plan[k]) return plan[k].end; return 0; };
    const moves = grid.map((col, c) => {
      if (!plan[c]) return null;
      const reel = reels[c], strip = reel.querySelector('.st-strip'), dur = (plan[c].end - now) / 1000;
      const n = Math.max(8, Math.round(SPEED * (turbo ? 1.4 : 1) * dur * easeAt.total) - 4);
      const L = strips[c].length, filler = Array.from({ length: n }, (_, k) => strips[c][(stops[c] + 4 + k) % L]);
      const fresh = col.map((s, r) => cellHtml(s, values[keyOf(c, r)], wilds[keyOf(c, r)]));
      const old = [...strip.children].map((el) => el.outerHTML);
      strip.innerHTML = fresh.join('') + filler.map((s) => cellHtml(s)).join('') + old.join('');
      reel.classList.remove('st-landed');
      const from = -(n + 4) * H;
      strip.style.transform = `translate3d(0,${from}px,0)`;
      return { c, reel, strip, from, start: now, end: plan[c].end, tease: plan[c].tease, done: false, y: from };
    }).filter(Boolean);
    return new Promise((resolve) => {
      let teasing = null, lastTick = 0, lastCell = null;
      const frame = (t) => {
        let left = 0;
        // A tick as each symbol passes the window (one stream for the machine, at most every 65 ms).
        const lead = moves.find((m) => !m.done);
        if (lead) {
          const cell = Math.floor(-lead.y / H);
          if (lastCell !== null && cell !== lastCell && t - lastTick > 65) { SFX.play('tick', lead.c); lastTick = t; }
          lastCell = cell;
        }
        for (const m of moves) {
          if (m.done) continue;
          if (hurry && m.end - t > 140 + m.c * 60) {
            m.from = m.y; m.start = t; m.end = t + 140 + m.c * 60; m.hurried = true;
          }
          const u = (t - m.start) / (m.end - m.start);
          m.y = m.hurried ? m.from * (1 - (1 - (1 - Math.min(1, u)) ** 3)) : m.from * (1 - easeAt(u));
          m.strip.style.transform = `translate3d(0,${m.y.toFixed(1)}px,0)`;
          m.reel.classList.toggle('st-moving', u > 0.04 && u < 0.86);
          if (m.tease && !m.hurried && u > 0 && !m.lit && t > prevEnd(m.c)) {
            m.lit = true; m.reel.classList.add('st-teasing');
            if (!teasing) teasing = SFX.play('tease');
          }
          if (u >= 1) {
            m.done = true; m.strip.style.transform = 'translate3d(0,0,0)';
            m.reel.classList.remove('st-moving', 'st-teasing'); m.reel.classList.add('st-landed');
            // Keep the four landed cells only, so the next spin starts from a short strip.
            while (m.strip.children.length > 4) m.strip.lastElementChild.remove();
            SFX.play('reel_stop', m.c);
            const keys = grid[m.c].map((s) => SYM(s).key);
            if (keys.includes('spike')) { SFX.play('scatter', m.c); m.reel.querySelectorAll('.st-k-spike').forEach((el) => el.classList.add('st-pop')); }
            if (keys.includes('fire')) { SFX.play('fireball', m.c); m.reel.querySelectorAll('.st-k-fire').forEach((el) => el.classList.add('st-pop')); }
            if (keys.includes('spicy')) { SFX.play('sizzle', m.c); m.reel.querySelectorAll('.st-k-spicy').forEach((el) => el.classList.add('st-pop')); }
          } else left += 1;
        }
        if (left) requestAnimationFrame(frame);
        else { teasing?.stop?.(); resolve(); }
      };
      requestAnimationFrame(frame);
    });
  }
  const cellAt = (c, r) => $$('.st-reel')[c]?.querySelectorAll('.st-cell')[r];
  function setCell(c, r, sym, v, m, cls = '') {
    const el = cellAt(c, r);
    if (!el) return null;
    const tmp = document.createElement('div');
    tmp.innerHTML = cellHtml(sym, v, m, cls);
    el.replaceWith(tmp.firstElementChild);
    return cellAt(c, r);
  }

  // ---- effects --------------------------------------------------------------------------------------------------------
  const fxLayer = () => $('#st-fx');
  function banner(text, sub = '', cls = '', ms = 1600) {
    const slot = $('#st-banner');
    if (!slot) return sleep(0);
    slot.innerHTML = `<div class="st-banner ${cls}"><b>${esc(text)}</b>${sub ? `<span>${esc(sub)}</span>` : ''}</div>`;
    return sleep(T(ms)).then(() => { if (slot.firstElementChild) slot.firstElementChild.classList.add('out'); return sleep(250); }).then(() => { slot.innerHTML = ''; });
  }
  function shake(cls = 'st-shake') {
    const cab = $('#st-cabinet');
    if (!cab) return;
    cab.classList.remove(cls); void cab.offsetWidth; cab.classList.add(cls);
    setTimeout(() => cab.classList.remove(cls), 900);
  }
  // Count the Win readout (and any other element) from one amount to another, ticking as it goes.
  function countTo(el, from, to, ms) {
    if (!el) return sleep(0);
    const t0 = performance.now(), d = T(ms);
    // A long count runs the count-up clip under it; a short one ticks.
    const run = d >= 850 ? SFX.play('count_run', 0, { gain: 0.6 }) : null;
    let ticks = 0;
    return new Promise((resolve) => {
      const step = (t) => {
        const u = hurry ? 1 : Math.min(1, (t - t0) / d), v = from + (to - from) * (1 - (1 - u) ** 2);
        el.textContent = money(Math.abs(v) < 0.005 ? 0 : v);
        if (!run && Math.floor(u * 14) > ticks) { ticks = Math.floor(u * 14); SFX.play('count', ticks); }
        if (u < 1) requestAnimationFrame(step); else { run?.stop(0.25); resolve(); }
      };
      requestAnimationFrame(step);
    });
  }
  let winShown = 0; // what the Win readout shows during a spin
  let vaultShown = null; // the JACKPOT shown across the top
  let keysShown = 0; // keys lit on the key square
  let feedTab = localStorage.getItem('fs.stFeed') === 'mine' ? 'mine' : 'all'; // the win feed: everyone's or yours
  let feedSeen = 0; // the newest feed entry seen, so the ticker only announces new ones
  const TEASE_FAKE = 0.05; // a spin without an event still shows an event's teaser this often (nothing follows)
  let encore = 0; // until when Onkey is singing his encore after a big moment
  async function addWin(amount, ms = 700) {
    const to = winShown + amount;
    await countTo($('#st-win'), winShown, to, ms);
    winShown = to;
  }
  function particles(kind, n, area = fxLayer()) {
    if (!area) return;
    const glyphs = { coin: ['🪙'], banana: ['🍌'], fire: ['🔥'], dust: ['💨'], candy: ['🍬', '🎃'], star: ['🌟', '✨', '⭐'] }[kind] || ['✨'];
    for (let i = 0; i < n; i++) {
      const p = document.createElement('span');
      p.className = `st-particle st-p-${kind}`;
      p.textContent = glyphs[i % glyphs.length];
      const x = Math.random() * 100, delay = Math.random() * 600, dur = 1100 + Math.random() * 900;
      p.style.left = `${x}%`;
      area.appendChild(p);
      p.animate([{ transform: `translate(0,-60px) rotate(0)`, opacity: 0 }, { opacity: 1, offset: 0.1 },
        { transform: `translate(${(Math.random() - 0.5) * 120}px, ${area.clientHeight + 80}px) rotate(${(Math.random() - 0.5) * 720}deg)`, opacity: 0.9 }],
      { duration: dur, delay, easing: 'cubic-bezier(.3,.1,.7,1)', fill: 'both' }).onfinish = () => p.remove();
      setTimeout(() => p.remove(), delay + dur + 200);
    }
  }
  // Teasers: a moment's hint of an event as the reels start (dust at the edge, a banana, a green flicker on reel 1, a
  // blade's glint). They come before every event, and TEASE_FAKE of the time before nothing at all.
  function teaser(kind) {
    const layer = fxLayer();
    if (!layer) return;
    if (kind === 'stampede') { particles('dust', 4); shake('st-rumble'); SFX.play('stamp'); }
    if (kind === 'rain') particles(data.october ? 'candy' : 'banana', 3);
    if (kind === 'clone') { const r = $$('.st-reel')[0]; r?.classList.add('st-flicker'); setTimeout(() => r?.classList.remove('st-flicker'), 700); SFX.play('warble'); }
    if (kind === 'slice') { layer.insertAdjacentHTML('beforeend', '<span class="st-glint" aria-hidden="true"></span>'); setTimeout(() => layer.querySelector('.st-glint')?.remove(), 900); }
  }
  // The Banana split: Onkey splits one symbol in two; it shows a ×2 and counts twice in the ways.
  async function bananaSplit(cell) {
    if (!cell) { await banner('Banana split!', 'Onkey missed. That reel had nothing to split.', 'st-b-rain', 1000); return; }
    const el = cellAt(cell[0], cell[1]);
    SFX.play('slash');
    el?.classList.add('st-split-cell', 'st-pop');
    el?.insertAdjacentHTML('beforeend', '<b class="st-x2" aria-hidden="true">×2</b>');
    await banner('Banana split!', 'That symbol counts twice', 'st-b-rain', 1100);
  }
  // The Music break: a pair of bongo drums drops onto a reel and Onkey plays them (synthesized drum hits only).
  async function musicBreak(cells) {
    banner('Music break!', 'Every reel with a bongo drum doubles the wins', 'st-b-music', 1100);
    for (const [c, r] of cells) {
      setCell(c, r, idx('drum'), null, null, 'st-drop');
      SFX.play('stamp', c);
      await sleep(T(160));
    }
    SFX.play('drumroll');
    await sleep(T(700));
  }
  // The beat, once the wins are counted: every reel showing a drum doubles them.
  async function beatTimes(beat) {
    const reels = $$('.st-reel').filter((reel) => reel.querySelector('.st-k-drum'));
    reels.forEach((reel, i) => setTimeout(() => { reel.classList.add('st-beat'); SFX.play('stamp', i); }, T(i * 220)));
    await sleep(T(reels.length * 220 + 200));
    shake();
    await banner(`Music break ×${beat}!`, `${reels.length} reel${reels.length === 1 ? '' : 's'} with a bongo drum`, 'st-b-music', 1300);
    reels.forEach((reel) => reel.classList.remove('st-beat'));
  }
  // The Stampede: Onkey charges across the reels in a cloud of dust while they spin.
  function stampedeRun() {
    const layer = fxLayer();
    if (!layer) return;
    SFX.play('stampede');
    const img = document.createElement('img');
    img.src = '/assets/stampede/stampede-onkey.png'; img.className = 'st-runner'; img.alt = '';
    layer.appendChild(img);
    const w = layer.clientWidth;
    img.animate([{ transform: `translate(${-260}px, 30px) rotate(-6deg)` }, { transform: `translate(${w * 0.25}px, -10px) rotate(5deg)`, offset: 0.25 },
      { transform: `translate(${w * 0.5}px, 30px) rotate(-6deg)`, offset: 0.5 }, { transform: `translate(${w * 0.75}px, -10px) rotate(5deg)`, offset: 0.75 },
      { transform: `translate(${w + 60}px, 30px) rotate(-6deg)` }], { duration: T(1500), easing: 'linear' }).onfinish = () => img.remove();
    const shakes = setInterval(() => shake('st-rumble'), 260);
    setTimeout(() => clearInterval(shakes), T(1400));
    particles('dust', 14);
  }
  async function stampWilds(cells) {
    banner('Stampede!', 'Onkey left wilds behind', 'st-b-stampede', 900);
    for (const [c, r] of cells) {
      const el = setCell(c, r, idx('wild'), null, null, 'st-stamp');
      if (el) SFX.play('stamp', c);
      await sleep(T(140));
    }
    await sleep(T(400));
  }
  // The clone ray. While the reels spin the scientist leans into the corner of the window (sciPeek()); once they stop
  // he copies reel 1 onto the reels after it, one at a time, with a beam of green light (cloneRay()).
  const SCI_LINES = ['If I cannot buy ze monkey, I will copy him. Starting wiz zis reel.', 'Hold still. Zis will only take a second.',
    'One reel, zree reels. Ze same reel. Science!', 'Ze copies are never quite right. Zis one will be.', 'Zat reel is mine now. And zat one.'];
  function sciPeek() {
    const layer = fxLayer();
    if (!layer || layer.querySelector('.st-sci')) return;
    layer.insertAdjacentHTML('beforeend', `<div class="st-sci" aria-hidden="true"><img src="/assets/scientist.png" alt="" draggable="false"><span class="st-sci-say">${esc(SCI_LINES[Math.floor(Math.random() * SCI_LINES.length)])}</span></div>`);
    SFX.play('warble');
  }
  async function cloneRay(ev, grid, values) {
    sciPeek();
    const layer = fxLayer(), reels = $$('.st-reel'), src = reels[ev.from];
    banner('The clone ray!', `The scientist copied reel ${ev.from + 1}`, 'st-b-clone', 1000);
    src?.classList.add('st-clone-src');
    await sleep(T(500));
    for (const c of ev.to) {
      const reel = reels[c];
      if (layer && src && reel) {
        const box = layer.getBoundingClientRect(), a = src.getBoundingClientRect(), b = reel.getBoundingClientRect();
        const beam = document.createElement('span');
        beam.className = 'st-beam';
        Object.assign(beam.style, { left: `${a.left + a.width / 2 - box.left}px`, top: `${a.top - box.top}px`, width: `${b.left + b.width / 2 - a.left - a.width / 2}px`, height: `${a.height}px` });
        layer.appendChild(beam);
        setTimeout(() => beam.remove(), T(700));
      }
      SFX.play('zap');
      reel?.classList.add('st-cloning');
      await sleep(T(260));
      for (let r = 0; r < 4; r++) setCell(c, r, grid[c][r], values[keyOf(c, r)], null, 'st-cloned');
      await sleep(T(320));
      reel?.classList.remove('st-cloning');
    }
    await sleep(T(450));
    src?.classList.remove('st-clone-src');
    const sci = layer?.querySelector('.st-sci');
    sci?.classList.add('out');
    setTimeout(() => sci?.remove(), 600);
  }
  // Strudel's slice: Man Strudel leans in and slashes a line across reels 2-4; every cell he cuts turns wild.
  async function strudelSlice(cells) {
    const layer = fxLayer();
    if (layer) {
      const img = document.createElement('img');
      img.src = '/assets/man_strudel.png'; img.className = 'st-strudel'; img.alt = '';
      layer.appendChild(img);
      img.animate([{ transform: 'translateX(120%)' }, { transform: 'translateX(0)', offset: 0.25 }, { transform: 'translateX(0)', offset: 0.8 }, { transform: 'translateX(120%)' }],
        { duration: T(2600), easing: 'ease-in-out' }).onfinish = () => img.remove();
    }
    await sleep(T(650));
    await strudelCut(cells);
    await banner("Strudel's slice!", 'Every cell he cut is wild', 'st-b-slice', 1100);
  }
  async function strudelCut(cells) {
    const layer = fxLayer();
    // The cut: a bright line through the cells' centres, running on past both ends.
    const pts = cells.map(([c, r]) => cellAt(c, r)?.getBoundingClientRect()).filter(Boolean);
    if (layer && pts.length === cells.length) {
      const box = layer.getBoundingClientRect(), mid = (b) => [b.left + b.width / 2 - box.left, b.top + b.height / 2 - box.top];
      const [x0, y0] = mid(pts[0]), [x1, y1] = mid(pts[pts.length - 1]), dx = (x1 - x0) * 0.22, dy = (y1 - y0) * 0.22;
      layer.insertAdjacentHTML('beforeend', `<svg class="st-cut" width="${box.width}" height="${box.height}" aria-hidden="true"><line x1="${x0 - dx}" y1="${y0 - dy}" x2="${x1 + dx}" y2="${y1 + dy}" pathLength="1"/></svg>`);
      setTimeout(() => layer.querySelector('.st-cut')?.classList.add('out'), T(1300));
      setTimeout(() => layer.querySelector('.st-cut')?.remove(), T(1700));
    }
    SFX.play('slash');
    shake();
    await sleep(T(250));
    for (const [c, r] of cells) {
      const el = setCell(c, r, idx('wild'), null, null, 'st-split');
      if (el) SFX.play('stamp', c);
      await sleep(T(130));
    }
  }
  async function rainDown(cells, values) {
    banner('Banana rain!', 'Ripe bananas falling', 'st-b-rain', 900);
    particles(data.october ? 'candy' : 'banana', 26);
    await sleep(T(500));
    for (const [c, r] of cells) {
      const el = setCell(c, r, idx('fire'), values[keyOf(c, r)], null, 'st-drop');
      if (el) SFX.play('fireball', c);
      await sleep(T(110));
    }
    await sleep(T(400));
  }
  // Onkey eats the spicy banana and breathes fire on the wins (the same show for the Inferno of older spins).
  function inferno(mult, spicy) {
    SFX.play('inferno');
    if (spicy) $$('.st-cell.st-k-spicy').forEach((el) => el.classList.add('st-pulse'));
    const layer = fxLayer();
    if (layer) {
      layer.insertAdjacentHTML('beforeend', '<div class="st-flames"></div>');
      setTimeout(() => layer.querySelector('.st-flames')?.remove(), T(1800));
    }
    particles('fire', 20);
    shake();
    return banner(spicy ? `Spicy! ×${mult}` : `Inferno ×${mult}`, spicy ? 'Onkey ate a spicy banana' : 'Onkey breathed on your win', 'st-b-inferno', 1300);
  }

  // ---- wins -----------------------------------------------------------------------------------------------------------
  let cycle = null;
  function stopCycle() {
    clearInterval(cycle); cycle = null;
    $$('.st-reward').forEach((el) => el.remove());
    $('#st-reels')?.classList.remove('st-showing');
    $$('.st-cell.st-hit').forEach((el) => el.classList.remove('st-hit'));
  }
  function light(cells) {
    $$('.st-cell.st-hit').forEach((el) => el.classList.remove('st-hit'));
    $('#st-reels')?.classList.add('st-showing');
    cells.forEach(([c, r]) => cellAt(c, r)?.classList.add('st-hit'));
  }
  const winText = (w, st) => `${SYM(w.symbol).name} on ${w.reels} reels · ${w.ways} way${w.ways === 1 ? '' : 's'} · ${money(w.mult * st)}`;
  // Shapes' names come with the machine; these are the ones older spins were paid for.
  const OLD_SHAPES = { three: '3 in a row', four: 'Four', square: 'Square', five: 'Five', block: 'Block', mega: 'Mega block' };
  const shapeName = (x) => M().shapes?.[x.kind]?.name || OLD_SHAPES[x.kind] || x.kind;
  const isFire = (x) => SYM(x.symbol).key === 'fire';
  const shapeText = (x, st, inf = 1) => `${shapeName(x)} of ${isFire(x) ? 'ripe bananas' : SYM(x.symbol).name}${x.x > 1 ? ` ×${x.x}` : ''} · ${money((x.mult / inf) * st)}`;
  // Everything that pays is drawn the same way (reward()): a gold ring round each of its cells, a line through them
  // for a shape (older spins' shapes have none), a chip naming it and, as it's paid, the amount popping up.
  const winLabel = (w) => `${SYM(w.symbol).name} · ${w.ways} way${w.ways === 1 ? '' : 's'}`;
  const shapeLabel = (x) => `${shapeName(x)}${isFire(x) ? ' of ripe bananas' : ''}${x.x > 1 ? ` ×${x.x}` : ''}`;
  const rewardOf = {
    win: (w, inf = 1) => ({ cells: w.cells, label: winLabel(w), mult: w.mult / inf }),
    shape: (x, inf = 1) => ({ cells: x.cells, strokes: x.strokes, label: shapeLabel(x), mult: x.mult / inf, fire: isFire(x) }),
  };
  function reward(it, cls = '') {
    const layer = fxLayer(), rs = it.cells.map(([c, r]) => cellAt(c, r)?.getBoundingClientRect());
    if (!layer || !rs.length || rs.some((b) => !b)) return null;
    const box = layer.getBoundingClientRect();
    const l = Math.min(...rs.map((b) => b.left)), t = Math.min(...rs.map((b) => b.top));
    const w = Math.max(...rs.map((b) => b.right)) - l, h = Math.max(...rs.map((b) => b.bottom)) - t;
    const at = new Map(it.cells.map(([c, r], k) => [keyOf(c, r), rs[k]]));
    const mid = ([c, r]) => { const b = at.get(keyOf(c, r)); return `${(b.left + b.width / 2 - l).toFixed(1)},${(b.top + b.height / 2 - t).toFixed(1)}`; };
    const rings = rs.map((b) => `<rect x="${(b.left - l + 5).toFixed(1)}" y="${(b.top - t + 5).toFixed(1)}" width="${(b.width - 10).toFixed(1)}" height="${(b.height - 10).toFixed(1)}" rx="14"/>`).join('');
    const lines = (it.strokes || []).map((st) => `<polyline pathLength="1" points="${st.map(mid).join(' ')}"/>`).join('');
    const el = document.createElement('div');
    el.className = `st-reward ${cls}`;
    Object.assign(el.style, { left: `${l - box.left}px`, top: `${t - box.top}px`, width: `${w}px`, height: `${h}px` });
    el.innerHTML = `<svg width="${w}" height="${h}" aria-hidden="true"><g class="st-rings">${rings}</g><g class="st-lines">${lines}</g><g class="st-lines st-lines-core">${lines}</g></svg>`
      + `<b>${esc(it.label)}</b>`;
    layer.appendChild(el);
    return el;
  }
  // Rewards one after another (a crowd of them goes faster): each lit, marked, and counted into the Win.
  async function reveal(items, st, { fast = false, sound = null } = {}) {
    const quick = fast || items.length > 4;
    for (const [k, it] of items.entries()) {
      stopCycle();
      light(it.cells);
      const el = reward(it);
      el?.insertAdjacentHTML('beforeend', `<em>+${money(it.mult * st)}</em>`);
      if (sound) SFX.play(sound(it), k);
      await addWin(it.mult * st, quick ? 160 : 380);
      await sleep(T(quick ? 120 : 380));
      el?.classList.add('out');
    }
    await sleep(T(200));
    clearOutlines();
  }
  const clearOutlines = () => $$('.st-reward').forEach((el) => el.remove());
  // What lights up in turn after a spin: its ways wins, then its shapes.
  const cycleItems = (r, st) => [...(r.wins || []).map((w) => ({ ...rewardOf.win(w), text: winText(w, st) })),
    ...(r.shapes || []).map((x) => ({ ...rewardOf.shape(x), text: shapeText(x, st) }))];
  // After a spin lands, its wins take turns lighting up until the next spin.
  function startCycle(items) {
    stopCycle();
    if (!items?.length) return;
    let k = 0;
    const show = () => {
      if (!$('#st-reels') || busy) return stopCycle();
      const it = items[k % items.length];
      light(it.cells);
      clearOutlines();
      reward(it, 'st-reward-still');
      const line = $('#st-winline span');
      if (line && items.length > 1) line.textContent = it.text;
      k += 1;
    };
    if (items.length > 1) cycle = setInterval(show, 1400);
    show();
  }
  // The ways wins, before what multiplies them (`inf`: a spicy banana, the Golden Onkey), which comes after the shapes.
  async function showWins(wins, inf, st) {
    const base = wins.reduce((a, w) => a + w.mult, 0) / inf;
    const under = base < 1; // pays back less than the bet: shown, but not celebrated
    $('#st-reels')?.classList.toggle('st-small', under);
    if (!under) SFX.play(base >= 5 ? 'win_medium' : 'win_small');
    const what = wins.length === 1 ? winText({ ...wins[0], mult: wins[0].mult / inf }, st) : `${wins.length} wins`;
    $('#st-winline').innerHTML = under
      ? `<b class="st-back">${money(base * st)} back</b><span>${esc(what)} · less than the bet</span>`
      : `<b class="st-won">${wins.map((w) => SYM(w.symbol).name).join(', ')}</b><span>${esc(what)}</span>`;
    await reveal(wins.map((w) => rewardOf.win(w, inf)), st);
  }

  // The shapes, the same way, with a rising mallet note each (a crackle for fireballs).
  async function showShapes(shapes, st, inf = 1, fast = false) {
    const total = shapes.reduce((a, x) => a + x.mult, 0) / inf;
    $('#st-reels')?.classList.remove('st-small');
    $('#st-winline').innerHTML = `<b class="${total >= 1 ? 'st-won' : 'st-back'}">${shapes.length === 1 ? `${esc(shapeName(shapes[0]))}!` : `${shapes.length} shapes!`}</b><span>${esc(shapes.map((x) => shapeText(x, st, inf)).slice(0, 3).join(' · '))}${shapes.length > 3 ? ' …' : ''}</span>`;
    // Three or more shapes on one spin are a combo: a tag over the reels while they play, and a run to finish.
    const combo = shapes.length >= 3 ? (shapes.length >= 5 ? 'Shape storm!' : shapes.length === 4 ? 'Quad shape!' : 'Triple shape!') : null;
    const layer = fxLayer();
    if (combo && layer) layer.insertAdjacentHTML('beforeend', `<b class="st-combo">${combo}<small>×${shapes.length}</small></b>`);
    await reveal(shapes.map((x) => rewardOf.shape(x, inf)), st, { fast, sound: (it) => (it.fire ? 'fireball' : 'shape') });
    if (combo) { SFX.play('retrigger'); await sleep(T(400)); layer?.querySelector('.st-combo')?.remove(); }
  }
  // The Vault Heist: the Golden Onkey had a key. Over the reels: the vault door and its locks, Onkey at the door, Man
  // Strudel (the scientist's brainbot) closing in from the right a step per lock, and the prize ladder. Each lock's dial
  // spins and opens or holds; the first that holds, Man Strudel gets there (and waves). Past every lock, the door.
  const HEIST_SCI = ['Strudel! Ze vault. Get it before ze monkey does.', 'Faster, Strudel. Zat jackpot belongs to science.',
    'Ze brainbot does not wave, Strudel. Stop waving.', 'Zis time, Strudel, no petting ze monkey.'];
  // The heist plays at full speed whatever Turbo says, and you pick the locks: click one (the order changes nothing;
  // each try takes the next result), or Onkey picks after PICK_IDLE_MS.
  const PICK_IDLE_MS = 20000;
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  async function heistShow(h) {
    const win = $('#st-window');
    if (!win) return;
    const st = h.stake;
    SFX.play('golden');
    await banner('The vault is open for business!', `${M().vault.keys} keys. Onkey heads for the vault.`, 'st-b-vault', 1500);
    const v = M().vault, rungs = v.pays.map((m, i) => ({ n: i, label: i === v.locks ? 'Every lock' : `${i} lock${i === 1 ? '' : 's'}`, pay: money(m * st) }));
    const ladder = () => `<div class="st-heist-row st-heist-door${h.door ? '' : ''}"><span>The door</span><b>JACKPOT ${vaultText()}</b></div>` + [...rungs].reverse().map((r) => `<div class="st-heist-row" data-rung="${r.n}"><span>${r.label}</span><b>${r.pay}</b></div>`).join('');
    win.insertAdjacentHTML('beforeend', `<div class="st-heist" id="st-heist"><div class="st-pick-head"><b>The Vault Heist</b><span id="st-heist-say">Pick a lock. Onkey cracks it before Man Strudel gets here.</span></div>
      <div class="st-heist-body"><div class="st-heist-stage"><div class="st-vault-door" id="st-vault-door"><i></i><i></i><i></i><i></i><span class="st-vault-hub">${emblem('vault', false)}</span></div>
        <div class="st-locks">${Array.from({ length: v.locks }, (_, i) => `<button type="button" class="st-lock" data-lock="${i}" aria-label="Lock ${i + 1}"><i></i></button>`).join('')}</div>
        <img class="st-heist-onkey" src="/assets/onkey-logo.png" alt="" draggable="false"><img class="st-heist-strudel" id="st-heist-strudel" src="/assets/man_strudel.png" alt="" draggable="false">
        <span class="st-heist-sci"><img src="/assets/scientist-face.png" alt="" draggable="false"><em>${esc(HEIST_SCI[Math.floor(Math.random() * HEIST_SCI.length)])}</em></span></div>
        <div class="st-heist-ladder" id="st-heist-ladder">${ladder()}</div></div>
      <div class="st-pick-foot"><button type="button" class="btn small" id="st-heist-auto">Pick for me</button><span class="muted small">Heist at a ${money(st)} bet (your keys' average). The vault door opens ${doorOdds(st)}, once every lock is open.</span></div></div>`);
    const box = $('#st-heist'), strudel = $('#st-heist-strudel'), say = $('#st-heist-say');
    const rung = (n) => { box.querySelectorAll('[data-rung]').forEach((el) => el.classList.toggle('on', Number(el.dataset.rung) === n)); };
    const step = (n) => strudel?.style.setProperty('--near', String(n));
    rung(0); step(0);
    // One lock at a time: wait for a click on an untried lock (or the idle timer, or Pick for me), then try it.
    const choose = () => new Promise((resolve) => {
      const untried = () => [...box.querySelectorAll('.st-lock:not(.open):not(.held)')];
      untried().forEach((b) => b.classList.add('ready'));
      let timer = null;
      const go = (btn) => {
        clearTimeout(timer);
        untried().forEach((b) => { b.classList.remove('ready'); b.onclick = null; });
        $('#st-heist-auto').onclick = null;
        resolve(btn);
      };
      untried().forEach((b) => { b.onclick = () => go(b); });
      $('#st-heist-auto').onclick = () => go(untried()[0]);
      timer = setTimeout(() => go(untried()[0]), PICK_IDLE_MS);
    });
    for (const [i, open] of h.locks.entries()) {
      const lock = await choose();
      if (say) say.textContent = 'Onkey listens for the click…';
      lock.classList.add('spinning');
      for (let k = 0; k < 8; k++) { SFX.play('tick', k); await wait(110); }
      lock.classList.remove('spinning');
      lock.classList.add(open ? 'open' : 'held');
      SFX.play(open ? 'retrigger' : 'defuse');
      step(i + 1);
      if (open) {
        rung(i + 1);
        if (say) say.textContent = i + 1 < v.locks ? 'Open! Pick the next one.' : 'Every lock is open. Now the door.';
        await wait(650);
      } else {
        strudel?.classList.add('there');
        if (say) say.textContent = 'That one held.';
        await banner('The lock held', 'Man Strudel got there first. He waved, and let Onkey keep what he found.', 'st-b-smoke', 2000);
      }
    }
    $('#st-heist-auto')?.remove();
    if (h.opened === v.locks) {
      const door = $('#st-vault-door');
      SFX.play('tease');
      door?.classList.add('st-door-try');
      await wait(1800);
      door?.classList.remove('st-door-try');
      if (h.door) {
        door?.classList.add('st-door-open');
        box.querySelector('.st-heist-door')?.classList.add('on');
        SFX.play('detonate');
        await wait(900);
      } else {
        // The near miss: the wheel turns almost all the way, catches on the last notch, and swings back.
        door?.classList.add('st-door-near');
        SFX.play('beep');
        await wait(1500);
        door?.classList.add('st-door-held');
        strudel?.classList.add('there');
        SFX.play('defuse');
        await banner('So close!', 'The door caught on the last notch. Man Strudel looks sorry about it.', 'st-b-smoke', 2200);
      }
    }
    box.remove();
    await addWin(h.cash, 900);
    if (h.door) await vaultShow(h.jackpot || 0);  // the show even when the jackpot was empty (it refills from the house's take)
  }
  // A bonus spin (the Big Experiment, Man Strudel's spins): the reels spin to what it landed on, `then` changes the
  // grid, and its wins and shapes are paid at its stake. The base spin's grid comes back afterwards.
  async function bonusSpin(sp, st, then) {
    $$('.st-cell.st-hit').forEach((el) => el.classList.remove('st-hit'));
    $('#st-reels')?.classList.remove('st-showing');
    const vals = valueMap(sp.values);
    await roll({ strips: M().strips, stops: sp.stops, grid: sp.landed, values: vals, lead: 520, gap: 160 });
    await then(vals);
    if (sp.wins.length) await reveal(sp.wins.map((w) => rewardOf.win(w)), st, { fast: sp.wins.length > 3 });
    if (sp.shapes.length) await reveal(sp.shapes.map((x) => rewardOf.shape(x)), st, { fast: true, sound: (it) => (it.fire ? 'fireball' : 'shape') });
    if (!sp.wins.length && !sp.shapes.length) await sleep(T(500));
  }
  // The Big Experiment: the evidence board is full; the scientist copies reel 1 onto three reels for one spin.
  async function experimentShow(e) {
    const base = shown, cab = $('#st-cabinet'), counter = $('#st-counter');
    SFX.play('bonus_start');
    await banner('The Big Experiment!', `${M().lab_full} photos on the board. Reel 1, copied ${e.to.length} times.`, 'st-b-clone', 1800);
    cab?.classList.add('st-lab');
    if (counter) { counter.classList.remove('hidden'); counter.innerHTML = `The Big Experiment · at a <b>${money(e.stake)}</b> bet (your photos' average)`; }
    await bonusSpin(e, e.stake, (vals) => cloneRay({ from: 0, to: e.to }, e.grid, vals));
    if (counter) counter.classList.add('hidden');
    await banner('Experiment over', `+${money(e.cash)}`, 'st-b-clone', 1300);
    cab?.classList.remove('st-lab');
    restoreGrid(base);
  }
  // Man Strudel switches sides: his friendship is full; for a few spins he stays and slashes wilds for you.
  async function defectShow(d) {
    const base = shown, cab = $('#st-cabinet'), counter = $('#st-counter'), layer = fxLayer();
    SFX.play('bonus_start');
    await banner('Man Strudel switches sides!', `${d.spins.length} spins with his knife arms on your side`, 'st-b-slice', 1900);
    cab?.classList.add('st-friend');
    layer?.insertAdjacentHTML('beforeend', '<img class="st-strudel st-strudel-stay" src="/assets/man_strudel.png" alt="" draggable="false">');
    let total = 0;
    for (const [k, sp] of d.spins.entries()) {
      if (counter) { counter.classList.remove('hidden'); counter.innerHTML = `Strudel spin <b>${k + 1}</b> of <b>${d.spins.length}</b> · won <b>${money(total)}</b>`; }
      await bonusSpin(sp, d.stake, async () => { for (const cut of sp.cuts) await strudelCut(cut); });
      total += sp.cash;
    }
    if (counter) counter.classList.add('hidden');
    layer?.querySelector('.st-strudel-stay')?.remove();
    await banner('Man Strudel waves goodbye', `+${money(d.cash)}`, 'st-b-slice', 1500);
    cab?.classList.remove('st-friend');
    restoreGrid(base);
  }
  // An achievement unlocked: its badge or title, and where to wear it.
  async function unlockShow(ids) {
    for (const id of ids) {
      const a = data.achievements?.find((x) => x.id === id);
      SFX.play('retrigger');
      confetti?.($('#st-window'), true);
      await banner(`Unlocked: ${a?.name || id}${a?.look.emoji ? ` ${a.look.emoji}` : ''}`, `A ${a?.slot === 'title' ? 'title' : 'badge'} for your name. Wear it from Onkey's Shop.`, 'st-b-vault', 2200);
    }
  }
  async function vaultShow(amount) {
    const music = SFX.play('jackpot_big');
    shake('st-shake-hard');
    const layer = fxLayer();
    if (layer) {
      layer.insertAdjacentHTML('beforeend', `<div class="st-jackpot-show st-vault-show"><small>The vault is open</small><b>JACKPOT</b><span>${money(amount)}</span></div>`);
      setTimeout(() => layer.querySelector('.st-vault-show')?.remove(), T(5200));
    }
    confetti?.($('#st-window'), true);
    setTimeout(() => confetti?.($('#st-window'), true), 700);
    setTimeout(() => confetti?.($('#st-window'), true), 1400);
    particles('coin', 60);
    particles('star', 30);
    $('#st-vault-pot')?.classList.add('st-pot-hit');
    setTimeout(() => $('#st-vault-pot')?.classList.remove('st-pot-hit'), 5000);
    await addWin(amount, 2600);
    vaultShown = 0; paintPots();
    await sleep(T(2600));
    music?.stop?.(1.2);
  }
  // The Golden Onkey's multiplier, once the ways and the shapes are counted: every win on the spin times three.
  async function goldenTimes(gx) {
    $$('.st-cell.st-k-golden').forEach((el) => el.classList.add('st-pulse'));
    $('#st-cabinet')?.classList.add('st-gold');
    SFX.play('golden');
    particles('star', 16);
    shake();
    await banner(`Golden Onkey ×${gx}!`, `Every win times ${gx}`, 'st-b-golden', 1300);
    $('#st-cabinet')?.classList.remove('st-gold');
  }
  // The Golden Onkey: he shines where he landed, the machine goes gold, and he pays just for being seen.
  async function goldenShow([c, r], st, spot) {
    const el = cellAt(c, r);
    el?.classList.add('st-golden-cell', 'st-pop');
    el?.insertAdjacentHTML('beforeend', '<span class="st-spotted" aria-hidden="true">Spotted!</span>');
    SFX.play('golden');
    $('#st-cabinet')?.classList.add('st-gold');
    if (el) confetti?.(el, true, { emoji: ['🌟', '✨'] });
    particles('star', 18);
    await banner('Golden Onkey!', `+${money(spot * st)} just for showing up, and every win ×3`, 'st-b-golden', 1900);
    await addWin(spot * st, 900);
    $('#st-cabinet')?.classList.remove('st-gold');
  }
  // The spike plant: the two spikes arm, the other reels spin again while it beeps faster, and it either goes off (a
  // third spike: free spins) or gets defused.
  async function plantShow(plant, values, peppers = {}) {
    plant.spikes.forEach(([c, r]) => cellAt(c, r)?.classList.add('st-planted'));
    SFX.play('beep');
    await banner('Spike planted', 'Looking for the third…', 'st-b-plant', 1000);
    let beeping = true, gap = 520;
    const beep = () => { if (!beeping) return; SFX.play('beep'); gap = Math.max(90, gap * 0.82); setTimeout(beep, hurry ? 400 : gap); };
    setTimeout(beep, 200);
    const grid = shown.grid.map((col) => col.slice());
    plant.reels.forEach((c, k) => { grid[c] = plant.landed[k]; });
    const stops = Array(5).fill(0);
    plant.reels.forEach((c, k) => { stops[c] = plant.stops[k]; });
    await roll({ strips: M().strips, stops, grid, values, wilds: peppers, lead: 500, gap: 200, only: plant.reels });
    beeping = false;
    $$('.st-planted').forEach((el) => el.classList.remove('st-planted'));
    if (plant.found) {
      SFX.play('detonate');
      shake('st-shake-hard');
      $('#st-window')?.classList.add('st-flash');
      setTimeout(() => $('#st-window')?.classList.remove('st-flash'), 700);
      particles('fire', 16);
      await banner('Detonated!', 'Three spikes', 'st-b-plant st-b-boom', 1100);
    } else {
      SFX.play('defuse');
      await banner('Defused', 'Not this time', 'st-b-smoke', 900);
    }
  }

  // ---- hold and spin ----------------------------------------------------------------------------------------------------
  // Back to the spin that started a bonus.
  function restoreGrid(base) {
    const reels = $('#st-reels');
    if (reels) reels.innerHTML = base.grid.map((col, c) => `<div class="st-reel" data-reel="${c}"><div class="st-strip">${col.map((s, r) => cellHtml(s, base.values[keyOf(c, r)])).join('')}</div></div>`).join('');
  }
  async function holdAndSpin(hold, st, base) {
    const cab = $('#st-cabinet'), counter = $('#st-counter'), R = M().hs_respins;
    SFX.play('bonus_start');
    $$('.st-cell.st-k-fire').forEach((el) => el.classList.add('st-pulse'));
    // The rules, once, before it starts.
    const slot = $('#st-banner');
    if (slot) {
      slot.innerHTML = `<div class="st-howto" title="Click to start"><b>Hold and spin</b><ul><li>Ripe bananas stick.</li>
        <li><b>${R} respins</b>; a new banana resets them.</li><li>Fill all 20 for <b>${M().full_grid_bonus}×</b>.</li></ul></div>`;
      await new Promise((resolve) => {
        const t = setTimeout(resolve, hurry ? 0 : T(2200));
        slot.firstElementChild?.addEventListener('click', () => { clearTimeout(t); resolve(); });
      });
      slot.innerHTML = '';
    }
    const music = SFX.play('bonus_music', 0, { loop: true, gain: 0.55 });
    cab?.classList.add('st-hold');
    stopCycle();
    const held = valueMap(hold.start);
    let total = Object.values(held).reduce((a, v) => a + v, 0) * st;
    for (let c = 0; c < 5; c++) {
      for (let r = 0; r < 4; r++) {
        const v = held[keyOf(c, r)];
        if (v) setCell(c, r, idx('fire'), v, null, 'st-held');
        else cellAt(c, r)?.replaceWith(Object.assign(document.createElement('div'), { className: 'st-cell st-empty', innerHTML: '<span class="st-empty-spin"></span>' }));
      }
    }
    const hud = (left, note = '') => `<span class="st-hud"><span class="st-hud-item"><small>Respins left</small><b class="st-hud-big">${left}</b></span>
      <span class="st-hud-item"><small>Bananas</small><b>${Object.keys(held).length} / 20</b></span><span class="st-hud-item"><small>Bonus so far</small><b>${money(total)}</b></span>${note ? `<em>${note}</em>` : ''}</span>`;
    if (counter) { counter.classList.remove('hidden'); counter.classList.add('st-counter-hud'); counter.innerHTML = hud(R); }
    let left = R;
    for (const round of hold.rounds) {
      holdBalance(displayBalance());
      const empties = [...$$('.st-cell.st-empty')];
      empties.forEach((el) => el.classList.add('st-respin'));
      SFX.play('spin');
      await sleep(T(800));
      const landing = Object.fromEntries(round.new.map(([c, r, v]) => [keyOf(c, r), v]));
      // The empty cells stop one after another, reel by reel; new fireballs drop in.
      for (let c = 0; c < 5; c++) {
        for (let r = 0; r < 4; r++) {
          const el = cellAt(c, r);
          if (!el?.classList.contains('st-empty')) continue;
          const v = landing[keyOf(c, r)];
          if (v) {
            held[keyOf(c, r)] = v; total += v * st;
            setCell(c, r, idx('fire'), v, null, 'st-held st-drop');
            SFX.play('lock', Object.keys(held).length);
            if (counter) counter.innerHTML = hud(left);
            await sleep(T(260));
          } else {
            el.classList.remove('st-respin');
            await sleep(T(35));
          }
        }
      }
      left = round.respins;
      const fresh = round.new.length;
      if (counter) {
        counter.innerHTML = hud(left, fresh ? `+${fresh} · reset` : '');
        counter.classList.remove('st-reset', 'st-down'); void counter.offsetWidth;
        counter.classList.add(fresh ? 'st-reset' : 'st-down');
      }
      await sleep(T(fresh ? 700 : 500));
    }
    if (hold.full) {
      shake('st-shake-hard');
      await banner('Full grid!', `+${money(M().full_grid_bonus * st)} bonus`, 'st-b-grand', 1800);
    }
    // Collect: every fireball pays into the Win, reel by reel.
    await banner('Collecting', `${Object.keys(held).length} ripe bananas`, 'st-b-hold', 900);
    for (let c = 0; c < 5; c++) {
      for (let r = 0; r < 4; r++) {
        const v = held[keyOf(c, r)];
        if (!v) continue;
        const el = cellAt(c, r);
        el?.classList.add('st-collect');
        await addWin(v * st, 200);
        el?.classList.remove('st-collect'); el?.classList.add('st-collected');
      }
    }
    if (hold.full) await addWin(M().full_grid_bonus * st, 1400);
    music?.stop?.(1);
    if (counter) { counter.classList.add('hidden'); counter.classList.remove('st-counter-hud', 'st-reset', 'st-down'); }
    await banner('Hold and spin paid', money(hold.cash * st), 'st-b-hold', 1500);
    cab?.classList.remove('st-hold');
    restoreGrid(base);
    return hold.cash * st;
  }

  // ---- the fire meter and the jackpot pick ----------------------------------------------------------------------------
  // Each ripe banana that landed is tossed into the basket, one notch each.
  async function feedMeter(cells, before, st) {
    const target = $('#st-meter-fill');
    paintMeter(before);
    if (!target || !cells.length) return;
    let n = before;
    const end = target.getBoundingClientRect();
    await Promise.all(cells.map(([c, r], k) => new Promise((resolve) => {
      setTimeout(() => {
        const from = cellAt(c, r)?.getBoundingClientRect();
        if (!from) { paintMeter(n += st); return resolve(); }
        const spark = document.createElement('span');
        spark.className = 'st-spark st-spark-banana';
        spark.innerHTML = '<svg viewBox="0 0 101 74" class="st-ripe st-ripe-yellow"><use href="#st-ripe"/></svg>';
        spark.style.left = `${from.left + from.width / 2}px`; spark.style.top = `${from.top + from.height / 2}px`;
        document.body.appendChild(spark);
        const dx = end.right - (from.left + from.width / 2), dy = end.top + end.height / 2 - (from.top + from.height / 2);
        spark.animate([{ transform: 'translate(-50%,-50%) scale(1.2)', opacity: 1 }, { transform: `translate(calc(-50% + ${dx * 0.5}px), calc(-50% + ${dy - 80}px)) scale(1)`, opacity: 1, offset: 0.5 },
          { transform: `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px)) scale(.5)`, opacity: 0.6 }], { duration: T(650), easing: 'ease-in' })
          .onfinish = () => { spark.remove(); paintMeter(n += st); SFX.play('spark', k); resolve(); };
        setTimeout(() => { if (spark.isConnected) { spark.remove(); paintMeter(n += st); resolve(); } }, T(650) + 400);
      }, T(k * 90));
    })));
  }
  // The pick: fifteen fireballs; each click cracks one open. Three of one jackpot wins it; three smokes and it's gone.
  // The server decided the ending before the first click (pick.picks is what each click shows, in order).
  function pickGame(pick, jackpots, st) {
    const win = $('#st-window');
    if (!win) return sleep(0);
    const kinds = M().pick_kinds, count = Object.fromEntries(kinds.map((k) => [k, 0]));
    const track = () => kinds.map((k) => `<div class="st-pk-row st-pk-${k}${count[k] >= 2 && k !== 'smoke' ? ' close' : ''}">${emblem(k)}<span class="st-pk-pips">${[0, 1, 2].map((i) => `<i class="${i < count[k] ? 'on' : ''}"></i>`).join('')}</span></div>`).join('');
    win.insertAdjacentHTML('beforeend', `<div class="st-pick" id="st-pick"><div class="st-pick-head"><b>Jackpot pick</b><span>Peel bananas. Three of one jackpot wins it; three smokes and it's gone.</span></div>
      <div class="st-pick-body"><div class="st-pick-board">${Array.from({ length: 15 }, (_, i) => `<button type="button" class="st-pk-ball" data-pk="${i}" aria-label="Banana ${i + 1}"><svg viewBox="0 0 101 74" class="st-ripe st-ripe-gold"><use href="#st-ripe"/></svg></button>`).join('')}</div>
      <div class="st-pick-track" id="st-pick-track">${track()}</div></div>
      <div class="st-pick-foot"><button type="button" class="btn small" id="st-pick-auto">Pick for me</button><span class="muted small">Jackpots now: ${['mini', 'minor', 'major', 'grand'].map((k) => `${EMBLEM[k].name} ${money(pots?.[k] ?? 0)}`).join(' · ')}</span></div></div>`);
    const box = $('#st-pick');
    let k = 0, done = false, auto = null, idle = null;
    return new Promise((resolve) => {
      const finish = async () => {
        done = true; clearInterval(auto); clearTimeout(idle);
        const rest = [...pick.rest];
        box.querySelectorAll('.st-pk-ball:not(.open)').forEach((b) => { const kind = rest.shift(); b.classList.add('open', 'missed'); b.innerHTML = emblem(kind || 'smoke'); });
        const winner = pick.outcome || 'smoke';
        box.querySelector(`.st-pk-row.st-pk-${winner}`)?.classList.add('won');
        box.querySelectorAll(`.st-pk-ball.open:not(.missed) .st-emb-${winner}`).forEach((e) => e.closest('.st-pk-ball').classList.add('won'));
        await sleep(T(1300));
        box.remove();
        const j = (jackpots || [])[0];
        if (j) { await jackpotShow(j); await addWin(j.amount, 1400); }
        else {
          SFX.play('smoke');
          particles('dust', 10);
          await banner('Up in smoke', 'No jackpot this time. Your meter starts again.', 'st-b-smoke', 2000);
        }
        resolve();
      };
      const open = (btn) => {
        if (done || !btn || btn.classList.contains('open') || k >= pick.picks.length) return;
        clearTimeout(idle);
        const kind = pick.picks[k++];
        count[kind] += 1;
        holdBalance(displayBalance());
        btn.classList.add('open', `st-pk-got-${kind}`);
        btn.innerHTML = emblem(kind);
        SFX.play(kind === 'smoke' ? 'smoke' : 'crack');
        if (kind !== 'smoke' && count[kind] === 2) SFX.play('scatter', 1);
        $('#st-pick-track').innerHTML = track();
        if (k >= pick.picks.length) finish();
        else idle = setTimeout(startAuto, 20000); // walked away: it picks for you
      };
      const startAuto = () => {
        if (auto || done) return;
        auto = setInterval(() => open(box.querySelector('.st-pk-ball:not(.open)')), T(450));
      };
      box.querySelectorAll('.st-pk-ball').forEach((b) => b.addEventListener('click', () => open(b)));
      $('#st-pick-auto')?.addEventListener('click', startAuto);
      SFX.play('bonus_start');
      if (hurry || autoLeft) startAuto();
      else idle = setTimeout(startAuto, 20000);
      const hurryCheck = setInterval(() => { if (done) clearInterval(hurryCheck); else if (hurry) startAuto(); }, 200);
    });
  }

  async function jackpotShow(j) {
    const music = SFX.play(j.key === 'grand' || j.key === 'major' ? 'jackpot_big' : 'jackpot');
    const big = j.key === 'grand' || j.key === 'major';
    shake(big ? 'st-shake-hard' : 'st-shake');
    const layer = fxLayer();
    if (layer) {
      layer.insertAdjacentHTML('beforeend', `<div class="st-jackpot-show st-pot-${j.key}"><small>Jackpot</small><b>${esc(j.name)}</b><span>${money(j.amount)}</span></div>`);
      setTimeout(() => layer.querySelector('.st-jackpot-show')?.remove(), T(big ? 3600 : 2400));
    }
    confetti?.($('#st-window'), big);
    if (big) setTimeout(() => confetti?.($('#st-window'), true), 600);
    particles('coin', big ? 40 : 18);
    $(`.st-pot-${j.key}`)?.classList.add('st-pot-hit');
    setTimeout(() => $(`.st-pot-${j.key}`)?.classList.remove('st-pot-hit'), 4000);
    // The jackpot drops back to its seed on the meter as it's paid.
    if (pots) {
      pots[j.key] = M().jackpots.find((x) => x.key === j.key).seed;
      if (potTarget) potTarget[j.key] = pots[j.key];
      paintPots();
    }
    await sleep(T(big ? 3200 : 2000));
    music?.stop?.(1.2);
  }

  // ---- free spins -------------------------------------------------------------------------------------------------------
  async function freeSpins(fs, st, base) {
    const cab = $('#st-cabinet'), counter = $('#st-counter');
    SFX.play('bonus_start');
    $$('.st-cell.st-k-spike').forEach((el) => el.classList.add('st-pulse'));
    const first = fs.spins.length - fs.spins.reduce((a, s) => a + s.retrigger, 0);
    await banner(`${first} free spins`, 'Wilds carry ×2 and ×3', 'st-b-free', 1800);
    const music = SFX.play('bonus_music', 0, { loop: true, gain: 0.5 });
    cab?.classList.add('st-free');
    stopCycle();
    let total = 0, awarded = first;
    if (counter) counter.classList.remove('hidden');
    for (const [k, spin] of fs.spins.entries()) {
      holdBalance(displayBalance());
      if (counter) counter.innerHTML = `Free spin <b>${k + 1}</b> of <b>${awarded}</b> · won <b>${money(total)}</b>`;
      $$('.st-cell.st-hit').forEach((el) => el.classList.remove('st-hit'));
      $('#st-reels')?.classList.remove('st-showing');
      const wilds = Object.fromEntries(spin.wilds.map(([c, r, m]) => [keyOf(c, r), m]));
      await roll({ strips: M().fs_strips, stops: spin.stops, grid: spin.grid, wilds, lead: 520, gap: 140 });
      // Ways and spikes first, then the shapes (a free spin's wilds multiply those too).
      const shapes = spin.shapes || [];
      if (spin.wins.length || spin.scatter) SFX.play(spin.mult >= 5 ? 'win_medium' : 'win_small');
      if (spin.wins.length) await reveal(spin.wins.map((w) => rewardOf.win(w)), st, { fast: true });
      if (spin.scatter) {
        light(spin.scatter.cells);
        await addWin(spin.scatter.mult * st, 450);
      }
      if (shapes.length) await showShapes(shapes, st, 1, true);
      total += spin.mult * st;
      if (spin.retrigger) {
        awarded += spin.retrigger;
        SFX.play('retrigger');
        await banner(`+${spin.retrigger} spins`, 'Three spikes again', 'st-b-free', 1100);
      }
      await sleep(T(spin.wins.length ? 550 : 250));
    }
    music?.stop?.(1);
    if (counter) counter.classList.add('hidden');
    await banner('Free spins paid', money(total), 'st-b-free', 1600);
    cab?.classList.remove('st-free');
    restoreGrid(base);
    return total;
  }

  // ---- a whole spin -------------------------------------------------------------------------------------------------------
  async function present(spin) {
    const r = spin.result, st = spin.stake, values = valueMap(r.values);
    winShown = 0;
    $('#st-win') && ($('#st-win').textContent = '0');
    $('#st-winline').innerHTML = r.daily ? '<b>Good luck</b><span>A free daily spin: the house paid the bet</span>' : '<b>Good luck</b>';
    const ev = r.event?.kind;
    // A hint of what's coming as the reels start (and now and then on a spin with nothing coming), then the event.
    const hinted = ev || (Math.random() < TEASE_FAKE ? ['stampede', 'rain', 'clone', 'slice'][Math.floor(Math.random() * 4)] : null);
    if (hinted) setTimeout(() => teaser(hinted), T(120));
    if (ev === 'stampede') setTimeout(stampedeRun, T(650));
    if (ev === 'clone') setTimeout(sciPeek, T(750));
    // The spicy bananas' peppers, shown on them as they land.
    const peppers = Object.fromEntries((r.spicy || []).map(([c, rr, m]) => [keyOf(c, rr), m]));
    // The landed cells carry their fireball values (a fireball a Stampede is about to cover, or on a reel the spike
    // plant respins, has none). The Golden Onkey is swapped into the cell he lands in.
    const first = r.landed.map((col) => col.slice()), firstValues = { ...values };
    if (r.golden) first[r.golden[0]][r.golden[1]] = idx('golden');
    const firstPeppers = { ...peppers };
    (r.plant?.reels || []).forEach((c) => { for (let k = 0; k < 4; k++) { delete firstValues[keyOf(c, k)]; delete firstPeppers[keyOf(c, k)]; } });
    await roll({ strips: M().strips, stops: r.stops, grid: first, values: firstValues, wilds: firstPeppers, extra: ev === 'stampede' || ev === 'clone' ? 1300 : 0 });
    shown = { grid: first, values: firstValues, wilds: firstPeppers, wins: [] };
    if (ev === 'stampede') await stampWilds(r.event.cells);
    if (ev === 'clone') await cloneRay(r.event, r.grid, values);
    if (ev === 'slice' && r.event.cells) await strudelSlice(r.event.cells);
    if (ev === 'rain') await rainDown(r.event.cells, values);
    if (ev === 'split') await bananaSplit(r.event.cell);
    if (ev === 'music') await musicBreak(r.event.cells);
    if (r.golden) await goldenShow(r.golden, st, r.spot);
    if (r.plant) await plantShow(r.plant, values, peppers);
    shown = { grid: r.grid, values, wilds: peppers, wins: r.wins };
    if (r.meter && $('#st-meter')) await feedMeter(r.values.map(([c, rr]) => [c, rr]), r.meter.before, st);
    // The bonuses play at their own pace (Turbo still shortens them): Skip only hurries the base spin.
    const inBonus = async (play) => { hurry = false; bonus = true; try { await play(); } finally { bonus = false; } };
    // Wins are shown before what multiplies them: a spicy banana (the Inferno, on older spins), then the Golden Onkey.
    const heat = r.heat || r.inferno || 1, gx = r.golden_x || 1, bx = r.beat || 1, inf = heat * gx * bx, shapes = r.shapes || [];
    if (r.wins.length) await showWins(r.wins, inf, st);
    if (shapes.length) await showShapes(shapes, st, inf);
    const base = [...r.wins, ...shapes].reduce((a, w) => a + w.mult, 0) / inf;
    if (heat > 1) {
      light([...r.wins, ...shapes].flatMap((w) => w.cells));
      await inferno(heat, !!r.heat);
      await addWin(base * (heat - 1) * st, 900);
    }
    if (bx > 1) {
      light([...r.wins, ...shapes].flatMap((w) => w.cells));
      await beatTimes(bx);
      await addWin(base * heat * (bx - 1) * st, 1000);
    }
    if (gx > 1) {
      light([...r.wins, ...shapes].flatMap((w) => w.cells));
      await goldenTimes(gx);
      await addWin(base * heat * bx * (gx - 1) * st, 1000);
    }
    if (r.key) await flyKey(r.golden ? cellAt(r.golden[0], r.golden[1]) : null, Math.min((r.keys?.before ?? keysShown) + 1, M().vault.keys));
    if (r.heist) {
      stopCycle();
      auto = 0; autoLeft = 0; // a heist stops Auto (Nonstop too): it's rare, and yours to play
      await inBonus(() => heistShow(r.heist));
      paintKeys(r.keys?.after ?? 0);
    }
    if (r.progress) paintProgress(r.progress);
    if (r.experiment) await inBonus(() => experimentShow(r.experiment));
    if (r.defect) await inBonus(() => defectShow(r.defect));
    if (r.unlocked?.length) await unlockShow(r.unlocked);
    if (r.scatter) {
      light(r.scatter.cells);
      SFX.play('scatter', 2);
      await banner(`${r.scatter.count} spikes`, `${money(r.scatter.mult * st)} and free spins`, 'st-b-free', 1000);
      await addWin(r.scatter.mult * st, 500);
    }
    if (r.hold) await inBonus(() => holdAndSpin(r.hold, st, shown));
    if (r.free_spins) await inBonus(() => freeSpins(r.free_spins, st, shown));
    if (r.pick) {
      await banner('Basket full!', 'Time to peel for a jackpot', 'st-b-pick', 1700);
      await inBonus(() => pickGame(r.pick, r.jackpots, st));
    }
    if (r.meter) paintMeter(r.meter.after);
    $('#st-winline').innerHTML = resultLine(spin);
    // The total, and a show for the big ones.
    const tier = TIERS.find(([m]) => r.mult >= m);
    const total = paidOf(spin);
    if (Math.abs(winShown - total) > 0.005) await countTo($('#st-win'), winShown, total, 400);
    winShown = total;
    if (tier && !r.heist?.jackpot) await bigWin(tier, spin.payout);
    // After the very biggest moments Onkey sings an encore (one of his recordings), until the next spin.
    if ((tier && tier[2] === 3) || r.heist?.jackpot || (r.jackpots || []).some((j) => j.key === 'major' || j.key === 'grand')) {
      const song = SFX.play('encore', 0, { gain: 0.9 });
      if (song) encore = Date.now() + song.duration * 1000;
    }
  }
  function bigWin([, label, level], amount) {
    const layer = fxLayer();
    const music = SFX.play(level === 3 ? 'win_epic' : 'win_big');
    if (level >= 2) shake(level === 3 ? 'st-shake-hard' : 'st-shake');
    confetti?.($('#st-window'), level >= 2);
    particles('coin', 14 + level * 12);
    if (!layer) return sleep(0);
    layer.insertAdjacentHTML('beforeend', `<div class="st-bigwin st-tier-${level}"><b>${label}</b><span id="st-big-amount">0</span></div>`);
    const show = layer.querySelector('.st-bigwin');
    return countTo($('#st-big-amount'), 0, amount, 1400 + level * 700).then(() => sleep(T(1100 + level * 300))).then(() => {
      show?.classList.add('out');
      music?.stop?.(1.2);
      return sleep(300);
    }).then(() => show?.remove());
  }

  // Spin, or skip ahead through the one playing.
  function press(force) {
    if (busy) { if (!bonus) hurry = true; return; }
    spin(force);
  }
  async function spin(force, daily = false) {
    if (busy || !state.me) return;
    const fresh = !pending;
    if (fresh) {
      const bytes = new Uint8Array(16); crypto.getRandomValues(bytes);
      const id = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
      // A daily spin's stake is the house's, at its own stake.
      remember(daily ? { stake: data.daily.stake, request_id: id, daily: true } : { stake, request_id: id });
    }
    const name = owner, request = pending;
    SFX.ready();
    holdBalance(fresh && !request.daily ? state.me.balance - request.stake : state.me.balance);
    SFX.stopLoops(); encore = 0; // a new spin ends Onkey's encore
    busy = true; hurry = false; error = ''; stopCycle();
    if (auto && !autoLeft) autoLeft = auto;
    draw();
    SFX.play('spin');
    $$('.st-reel').forEach((el) => el.classList.add('st-windup'));
    // Every spin grows the jackpots; the meters show it straight away (a win is shown when it's paid).
    if (pots) { for (const j of data.pots) pots[j.key] += j.grow * request.stake; paintPots(); }
    let out = null;
    try {
      out = await api('/api/stampede/spin', { method: 'POST', body: JSON.stringify({ ...request, ...(force ? { force } : {}) }) });
      if (owner !== name) return;
      remember(null);
      $$('.st-reel').forEach((el) => el.classList.remove('st-windup'));
      await present(out.spin);
      last = out.spin;
      if (state.me?.name === name) state.me.balance = out.balance;
      data.meter = out.meter; meterShown = out.meter.heat;
      if (out.jackpot != null) { data.jackpot = out.jackpot; vaultShown = out.jackpot; }
      if (out.keys) { data.keys = out.keys; keysShown = out.keys.stakes.length; }
      if (out.daily) data.daily = out.daily;
      if (out.progress) data.progress = out.progress;
    } catch (e) {
      if (owner === name) {
        if (e.status >= 400 && e.status < 500) remember(null);
        error = pending ? 'The result could not be confirmed. Check last spin to recover it without paying twice.' : e.message;
        auto = 0; autoLeft = 0;
      }
    } finally {
      if (encore < Date.now()) SFX.stopLoops();
      busy = false; hurry = false;
      releaseBalance();
      if (out && owner === name) {
        potTarget = Object.fromEntries(out.pots.map((p) => [p.key, p.size]));
        await Promise.all([load().catch(() => {}), loadMe()]);
        note(out.spin);
      }
      if (state.view === 'stampede') {
        draw();
        if (last) startCycle(cycleItems(last.result, last.stake));
        $('#st-spin')?.focus();
      }
      // Auto: the next spin, unless a bonus, a jackpot or a big win just happened (those deserve a pause), or the
      // credits ran short.
      if (auto && last && out) {
        const r = last.result, broke = !state.me || state.me.balance < stake;
        const stop = broke || r.heist || (!nonstop && (r.free_spins || r.hold || r.mult >= 10));
        autoLeft = stop ? 0 : autoLeft - 1;
        if (autoLeft > 0 && state.view === 'stampede' && !document.hidden) setTimeout(() => { if (!busy && autoLeft > 0) spin(); }, T(500));
        else { auto = 0; autoLeft = 0; if (state.view === 'stampede') draw(); }
      }
    }
  }
  function note(s) {
    const r = s.result;
    window.FiveOnkey?.note('stampede', {
      stake: s.stake, payout: s.payout, multiplier: r.mult, free_spins: !!r.free_spins, hold: !!r.hold,
      jackpot: r.jackpots?.length ? r.jackpots[r.jackpots.length - 1].name : null, event: r.event?.kind || null, inferno: r.heat || r.inferno,
      golden: !!r.golden, plant: r.plant ? (r.plant.found ? 'detonated' : 'defused') : null,
      heist: r.heist ? r.heist.opened : null, vault: r.heist?.jackpot || 0, experiment: !!r.experiment, defect: !!r.defect,
    });
  }

  function bind(viewEl) {
    $$('[data-st-stake]', viewEl).forEach((b) => b.addEventListener('click', () => {
      stake = Number(b.dataset.stStake); error = '';
      localStorage.setItem('fs.stStake', String(stake));
      draw(); $(`[data-st-stake="${stake}"]`)?.focus();
    }));
    $('#st-spin', viewEl)?.addEventListener('click', () => press());
    $('#st-daily', viewEl)?.addEventListener('click', () => { if (!busy) spin(null, true); });
    $$('[data-st-feed]', viewEl).forEach((b) => b.addEventListener('click', () => {
      feedTab = b.dataset.stFeed; localStorage.setItem('fs.stFeed', feedTab);
      $$('[data-st-feed]').forEach((x) => { x.classList.toggle('on', x === b); x.setAttribute('aria-pressed', String(x === b)); });
      const box = $('#st-feed-list');
      if (box) box.innerHTML = feedRows(feedTab === 'mine' ? data.my_feed : data.feed);
    }));
    $$('[data-st-force]', viewEl).forEach((b) => b.addEventListener('click', () => press(b.dataset.stForce)));
    $('#st-auto', viewEl)?.addEventListener('click', () => {
      auto = AUTO_STEPS[(AUTO_STEPS.indexOf(auto) + 1) % AUTO_STEPS.length];
      if (!busy) autoLeft = 0;
      else autoLeft = auto;
      const b = $('#st-auto');
      b.classList.toggle('on', !!auto); b.setAttribute('aria-pressed', String(!!auto));
      b.querySelector('b').textContent = auto ? String(auto) : 'off';
    });
    $('#st-nonstop', viewEl)?.addEventListener('click', () => {
      nonstop = !nonstop; localStorage.setItem('fs.stNonstop', nonstop ? '1' : '0');
      const b = $('#st-nonstop');
      b.classList.toggle('on', nonstop); b.setAttribute('aria-pressed', String(nonstop));
      b.querySelector('b').textContent = nonstop ? 'on' : 'off';
    });
    $('#st-turbo', viewEl)?.addEventListener('click', () => {
      turbo = !turbo; localStorage.setItem('fs.stTurbo', turbo ? '1' : '0');
      const b = $('#st-turbo');
      b.classList.toggle('on', turbo); b.setAttribute('aria-pressed', String(turbo));
      b.querySelector('b').textContent = turbo ? 'on' : 'off';
    });
    $('#st-sound', viewEl)?.addEventListener('click', (e) => {
      muted = !muted; localStorage.setItem('fs.stMuted', muted ? '1' : '0');
      if (muted) SFX.stopLoops();
      const b = e.currentTarget, label = muted ? 'Turn sound on' : 'Mute sound';
      b.innerHTML = window.speakerIcon(muted, 20); b.setAttribute('aria-pressed', String(!muted)); b.setAttribute('aria-label', label); b.title = label;
    });
    if (!busy && last && state.view === 'stampede') startCycle(cycleItems(last.result, last.stake));
  }
  return { init, load, view, bind };
})();
