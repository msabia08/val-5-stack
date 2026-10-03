/* 5-Stack Tracker: Onkey's Arcade. Three small canvas games (Banana Catch, Onkey Says, Spike Dash) that cost bananas
 * to play, their high-score boards, and a little Web Audio engine: synthesized coin drops, blips and a bongo chiptune
 * in C♯ minor (the key Onkey sings in), plus Onkey's song (web/assets/onkey-song.wav), always played whole.
 *
 * A play is paid for on the server first (/api/arcade/start hands back a token) and its score is sent back with that
 * token (/api/arcade/finish), which checks it's plausible. The only prize is the high-score board.
 * app.js calls FiveArcade.init() once, like FiveShop. Plain JS, no dependencies.
 */
window.FiveArcade = (() => {
  'use strict';

  let state, $, $$, api, draw, esc, fmt, toast, nameHtml, loadMe;
  const BANANA = '🍌';
  const W = 480, H = 320; // every game draws at this size; CSS scales the canvas up with chunky pixels

  function init(ctx) {
    ({ state, $, $$, api, draw, esc, fmt, toast, nameHtml, loadMe } = ctx);
  }
  const load = async () => { state.arcade = await api('/api/arcade'); };

  // ---- sound ---------------------------------------------------------------------
  // Everything is made on the fly with oscillators and a little noise, except Onkey's song. Browsers only allow audio
  // after a click or key press, so the context is created on the first coin.
  const N = { C1: 34.65, B1: 61.74, Cs2: 69.3, E2: 82.41, Fs2: 92.5, Gs2: 103.83, B2: 123.47, Cs3: 138.59,
    Cs4: 277.18, E4: 329.63, Fs4: 369.99, Gs4: 415.3, B4: 493.88, Cs5: 554.37, E5: 659.25, F5: 698.46, Fs5: 739.99, Gs5: 830.61, B5: 987.77, Cs6: 1108.73, E6: 1318.51 };
  // The sound buttons' label: the site's speaker icon (common.js) and what it's set to.
  const soundText = () => `${window.speakerIcon(Sound.muted, 16)} ${Sound.muted ? 'Sound off' : 'Sound on'}`;
  const Sound = (() => {
    let ac = null, master, sfx, musicBus, songBuf = null, songLoading = null, noiseBuf = null;
    let muted = false;
    try { muted = localStorage.getItem('fs.arcadeMute') === '1'; } catch (e) { /* storage blocked */ }
    function ctx() {
      if (!ac) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return null;
        ac = new AC();
        master = ac.createGain(); master.gain.value = muted ? 0 : 0.8; master.connect(ac.destination);
        sfx = ac.createGain(); sfx.gain.value = 0.55; sfx.connect(master);
        musicBus = ac.createGain(); musicBus.gain.value = 0.32; musicBus.connect(master);
      }
      if (ac.state === 'suspended') ac.resume();
      return ac;
    }
    function tone({ type = 'square', f, f2, t = 0, dur = 0.1, vol = 0.3, bus, attack = 0.004 }) {
      const c = ctx();
      if (!c) return;
      const o = c.createOscillator(), g = c.createGain(), at = c.currentTime + t;
      o.type = type;
      o.frequency.setValueAtTime(f, at);
      if (f2) o.frequency.exponentialRampToValueAtTime(f2, at + dur);
      g.gain.setValueAtTime(0.0001, at);
      g.gain.exponentialRampToValueAtTime(vol, at + attack);
      g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
      o.connect(g).connect(bus || sfx);
      o.start(at);
      o.stop(at + dur + 0.02);
    }
    function noise({ t = 0, dur = 0.05, vol = 0.2, hp = 4000, bus }) {
      const c = ctx();
      if (!c) return;
      if (!noiseBuf) {
        noiseBuf = c.createBuffer(1, c.sampleRate * 0.5, c.sampleRate);
        const d = noiseBuf.getChannelData(0);
        for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
      }
      const s = c.createBufferSource(), f = c.createBiquadFilter(), g = c.createGain(), at = c.currentTime + t;
      s.buffer = noiseBuf; f.type = 'highpass'; f.frequency.value = hp;
      g.gain.setValueAtTime(vol, at); g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
      s.connect(f).connect(g).connect(bus || sfx);
      s.start(at); s.stop(at + dur + 0.02);
    }
    // A bongo: a sine that drops in pitch. The monkey in the chiptune.
    const bongo = (t, hi, bus, vol = 0.55) => tone({ type: 'sine', f: hi ? 460 : 300, f2: hi ? 230 : 150, t, dur: 0.14, vol, bus });
    const arp = (notes, step, opts = {}) => notes.forEach((f, i) => tone({ f, t: i * step, dur: step * 1.6, vol: 0.22, ...opts }));

    const fx = {
      coin: () => { noise({ dur: 0.04, vol: 0.25, hp: 1500 }); tone({ f: N.B5, dur: 0.09, vol: 0.25 }); tone({ f: N.E6, t: 0.08, dur: 0.38, vol: 0.25 }); },
      start: () => arp([N.Cs5, N.E5, N.Gs5, N.Cs6], 0.07),
      count: (go) => tone({ f: go ? N.Cs6 : N.Cs5, dur: go ? 0.3 : 0.12, vol: 0.22 }),
      catch: (combo = 0) => tone({ f: [N.Cs5, N.E5, N.Fs5, N.Gs5, N.B5][Math.min(4, combo)], f2: N.Cs6, dur: 0.07, vol: 0.18 }),
      golden: () => { arp([N.Gs5, N.B5, N.Cs6, N.E6], 0.045, { type: 'triangle' }); bongo(0, true); },
      hurt: () => { tone({ type: 'sawtooth', f: 240, f2: 70, dur: 0.32, vol: 0.28 }); noise({ dur: 0.18, vol: 0.2, hp: 600 }); },
      jump: (second) => tone({ f: second ? 420 : 300, f2: second ? 1000 : 720, dur: 0.12, vol: 0.16 }),
      pickup: () => tone({ type: 'triangle', f: N.E5, f2: N.B5, dur: 0.1, vol: 0.25 }),
      perfect: () => bongo(0, true, null, 0.6),
      good: () => bongo(0, false, null, 0.5),
      miss: () => tone({ type: 'square', f: 110, f2: 80, dur: 0.12, vol: 0.12 }),
      gameOver: () => [N.Gs4, N.Fs4, N.E4, N.Cs4].forEach((f, i) => tone({ type: 'triangle', f, t: i * 0.2, dur: 0.28, vol: 0.3 })),
      fanfare: () => { arp([N.Cs5, N.F5, N.Gs5, N.Cs6, N.Gs5, N.Cs6], 0.09); bongo(0.55, true); bongo(0.7, false); },
    };

    // A two-bar bongo chiptune loop in C♯ minor pentatonic: triangle bass, a quiet square riff, bongos and a hat.
    // tempo() changes speed on the fly (Spike Dash speeds up as you go).
    const BASS = [N.Cs2, 0, N.Cs2, 0, N.E2, 0, N.Cs2, N.B1, N.Fs2, 0, N.Fs2, 0, N.Gs2, 0, N.E2, 0];
    const LEAD = [N.Cs5, 0, N.E5, 0, N.Fs5, N.E5, 0, N.Cs5, 0, N.B4, N.Cs5, 0, N.Gs4, 0, 0, 0,
      N.Cs5, 0, N.E5, 0, N.Gs5, N.Fs5, 0, N.E5, 0, N.Cs5, N.B4, 0, N.Cs5, 0, 0, 0];
    function music(bpm) {
      const c = ctx();
      if (!c) return { stop() {}, tempo() {} };
      let step = 60 / bpm / 2, next = c.currentTime + 0.12, i = 0, on = true;
      const tick = () => {
        while (on && next < c.currentTime + 0.25) {
          const at = next - c.currentTime, b = BASS[i % 16], l = LEAD[i % 32];
          if (b) tone({ type: 'triangle', f: b, t: at, dur: step * 1.8, vol: 0.5, bus: musicBus });
          if (l) tone({ type: 'square', f: l, t: at, dur: step * 0.9, vol: 0.09, bus: musicBus });
          if (i % 4 === 2) bongo(at, (i / 4) % 2 < 1, musicBus, 0.45);
          if (i % 8 === 7) bongo(at, true, musicBus, 0.3);
          if (i % 2 === 1) noise({ t: at, dur: 0.03, vol: 0.06, hp: 7000, bus: musicBus });
          next += step; i++;
        }
      };
      tick();
      const id = setInterval(tick, 25);
      return { stop() { on = false; clearInterval(id); }, tempo(b) { step = 60 / b / 2; } };
    }

    // Onkey's song, whole. rate > 1 plays it faster and higher (Onkey Says' later verses).
    function loadSong() {
      const c = ctx();
      if (!c) return Promise.resolve(null);
      if (songBuf) return Promise.resolve(songBuf);
      songLoading ||= fetch('/assets/onkey-song.wav').then((r) => r.arrayBuffer()).then((b) => new Promise((ok, bad) => c.decodeAudioData(b, ok, bad)))
        .then((buf) => (songBuf = buf)).catch(() => null);
      return songLoading;
    }
    async function song({ rate = 1, when = 0, vol = 1 } = {}) {
      const buf = await loadSong(), c = ctx();
      if (!buf || !c) return null;
      const s = c.createBufferSource(), g = c.createGain();
      s.buffer = buf; s.playbackRate.value = rate; g.gain.value = vol;
      s.connect(g).connect(master);
      const at = Math.max(c.currentTime + 0.02, when);
      s.start(at);
      return { source: s, at, stop: () => { try { s.stop(); } catch (e) { /* already stopped */ } } };
    }

    return {
      ctx, fx, music, song, loadSong,
      now: () => (ac ? ac.currentTime : 0),
      latency: () => (ac ? (ac.outputLatency || ac.baseLatency || 0) : 0),
      get muted() { return muted; },
      toggleMute() {
        muted = !muted;
        try { localStorage.setItem('fs.arcadeMute', muted ? '1' : '0'); } catch (e) { /* storage blocked */ }
        if (master) master.gain.value = muted ? 0 : 0.8;
        return muted;
      },
      pause: () => ac && ac.state === 'running' && ac.suspend(),
      resume: () => ac && ac.state === 'suspended' && ac.resume(),
    };
  })();

  // ---- art -------------------------------------------------------------------------
  const img = (src) => { const i = new Image(); i.src = src; return i; };
  const ART = { onkey: img('/assets/onkey-logo.png'), greg: img('/assets/greg-drop.png') };
  const PIXEL = 'bold 14px ui-monospace, "Cascadia Code", Consolas, monospace';
  function text(g, s, x, y, { size = 14, color = '#fff', align = 'left', glow = null } = {}) {
    g.font = PIXEL.replace('14px', `${size}px`);
    g.textAlign = align; g.textBaseline = 'middle';
    if (glow) { g.shadowColor = glow; g.shadowBlur = 8; }
    g.fillStyle = '#000'; g.fillText(s, x + 2, y + 2);
    g.fillStyle = color; g.fillText(s, x, y);
    g.shadowBlur = 0;
  }
  function emoji(g, e, x, y, size) {
    g.font = `${size}px "Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", sans-serif`;
    g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText(e, x, y);
  }
  const drawOnkey = (g, x, y, w, rot = 0) => {
    if (!ART.onkey.complete) return;
    const h = w * (72 / 124);
    g.save(); g.translate(x, y); g.rotate(rot); g.drawImage(ART.onkey, -w / 2, -h / 2, w, h); g.restore();
  };

  // ---- the games -------------------------------------------------------------------------
  // Each game is make(env) -> { update(dt), render(g), key(code, down), tap(x, y, down), score, over }.
  // env: { sound, onEnd() }. dt is in seconds, clamped so a hitch never teleports anything.
  const GAMES = {};

  // Banana Catch: move Onkey (arrows / A D / drag), catch bananas, dodge Gregs. 60 s, 3 lives, combos up to x3.
  GAMES.catch = (env) => {
    const s = { x: W / 2, t: 0, lives: 3, score: 0, combo: 0, items: [], spawn: 0.6, pops: [], left: false, right: false, target: null, over: false, flash: 0 };
    const music = env.sound.music(132);
    const ROUND = 60;
    const leaves = Array.from({ length: 9 }, (_, i) => ({ x: i * 60 + (i % 2) * 20, r: 34 + (i % 3) * 10 }));
    function spawn() {
      const k = s.t / ROUND, r = Math.random();
      const kind = r < 0.05 ? 'gold' : r < 0.2 + k * 0.17 ? 'greg' : 'banana';
      s.items.push({ kind, x: 24 + Math.random() * (W - 48), y: -20, vy: 90 + k * 150 + Math.random() * 40, spin: Math.random() * 6 });
    }
    return {
      get score() { return s.score; }, get over() { return s.over; },
      hud: () => [`TIME ${Math.max(0, Math.ceil(ROUND - s.t))}`, `${'♥'.repeat(s.lives)}${'·'.repeat(3 - s.lives)}`],
      update(dt) {
        if (s.over) return;
        s.t += dt;
        const speed = 330;
        if (s.target != null) s.x += Math.max(-speed * dt, Math.min(speed * dt, s.target - s.x));
        else s.x += ((s.right ? 1 : 0) - (s.left ? 1 : 0)) * speed * dt;
        s.x = Math.max(50, Math.min(W - 50, s.x));
        s.spawn -= dt;
        if (s.spawn <= 0) { spawn(); s.spawn = Math.max(0.28, 0.8 - (s.t / ROUND) * 0.5) * (0.7 + Math.random() * 0.6); }
        const catchY = H - 58;
        s.items = s.items.filter((it) => {
          it.y += it.vy * dt; it.spin += dt * 3;
          if (it.y > catchY - 12 && it.y < catchY + 14 && Math.abs(it.x - s.x) < 56) {
            if (it.kind === 'greg') {
              s.lives--; s.combo = 0; s.flash = 0.35; env.sound.fx.hurt();
              s.pops.push({ x: it.x, y: it.y, txt: 'OUCH', c: '#ff5470', t: 0.8 });
              if (s.lives <= 0) { s.over = true; music.stop(); env.sound.fx.gameOver(); env.onEnd(); }
            } else {
              s.combo++;
              const mult = s.combo >= 20 ? 3 : s.combo >= 8 ? 2 : 1, pts = (it.kind === 'gold' ? 100 : 10) * mult;
              s.score += pts;
              if (it.kind === 'gold') env.sound.fx.golden(); else env.sound.fx.catch(Math.floor(s.combo / 4));
              s.pops.push({ x: it.x, y: it.y - 10, txt: `+${pts}`, c: it.kind === 'gold' ? '#ffd700' : '#fff', t: 0.7 });
            }
            return false;
          }
          if (it.y > H + 20) { if (it.kind !== 'greg') s.combo = 0; return false; }
          return true;
        });
        s.pops.forEach((p) => { p.t -= dt; p.y -= 30 * dt; });
        s.pops = s.pops.filter((p) => p.t > 0);
        s.flash = Math.max(0, s.flash - dt);
        if (s.t >= ROUND && !s.over) { s.over = true; music.stop(); env.sound.fx.fanfare(); env.onEnd(); }
      },
      render(g) {
        const sky = g.createLinearGradient(0, 0, 0, H);
        sky.addColorStop(0, '#0d2b1a'); sky.addColorStop(1, '#1f5a34');
        g.fillStyle = sky; g.fillRect(0, 0, W, H);
        g.fillStyle = '#123d22';
        leaves.forEach((l) => { g.beginPath(); g.ellipse(l.x, 0, l.r, l.r * 0.7, 0, 0, Math.PI * 2); g.fill(); });
        g.strokeStyle = '#2f7d3f'; g.lineWidth = 3;
        [60, 190, 400].forEach((x, i) => { g.beginPath(); g.moveTo(x, 0); g.quadraticCurveTo(x + 14, 60 + i * 20, x - 6, 110 + i * 25); g.stroke(); });
        g.fillStyle = '#2b1a0e'; g.fillRect(0, H - 22, W, 22);
        g.fillStyle = '#3d2614'; for (let x = 0; x < W; x += 24) g.fillRect(x, H - 22, 12, 4);
        s.items.forEach((it) => {
          g.save(); g.translate(it.x, it.y); g.rotate(Math.sin(it.spin) * 0.4);
          if (it.kind === 'greg') { if (ART.greg.complete) g.drawImage(ART.greg, -17, -17, 34, 34); }
          else {
            if (it.kind === 'gold') { g.shadowColor = '#ffd700'; g.shadowBlur = 16; g.fillStyle = 'rgba(255,215,0,.35)'; g.beginPath(); g.arc(0, 0, 16, 0, 7); g.fill(); g.shadowBlur = 0; }
            emoji(g, BANANA, 0, 0, it.kind === 'gold' ? 26 : 22);
          }
          g.restore();
        });
        drawOnkey(g, s.x, H - 50, 112, (s.right - s.left) * 0.08);
        s.pops.forEach((p) => text(g, p.txt, p.x, p.y, { size: 13, color: p.c, align: 'center' }));
        if (s.combo >= 8) text(g, `COMBO x${s.combo >= 20 ? 3 : 2}`, W / 2, 46, { size: 12, color: '#f5c518', align: 'center' });
        if (s.flash) { g.fillStyle = `rgba(255,60,80,${s.flash})`; g.fillRect(0, 0, W, H); }
      },
      key(code, down) {
        if (['ArrowLeft', 'KeyA'].includes(code)) s.left = down;
        if (['ArrowRight', 'KeyD'].includes(code)) s.right = down;
        if (down) s.target = null;
      },
      tap(x, y, down) { s.target = down ? x : null; },
      stop() { music.stop(); },
    };
  };

  // Onkey Says: a rhythm game on Onkey's song. Notes are the song's 11 syllables (their times measured from the
  // recording); hit the lane as each lands. Three verses at 1x, 1.15x and 1.3x speed, lanes mirrored on verse two.
  const SONG_NOTES = [
    [0.32, 0.19, 0], [0.71, 0.23, 2], [1.43, 0.34, 1], [2.13, 0.22, 0], [2.53, 0.24, 2], [3.08, 0.31, 1], // the chant
    [3.93, 0.20, 1], [4.34, 0.23, 2], [4.88, 0.22, 0], [5.41, 0.24, 0], [6.30, 0.71, 1, true], // the tune, ending on a held note
  ];
  const SONG_LENGTH = 7.54;
  GAMES.says = (env) => {
    const LANES = [W / 2 - 110, W / 2, W / 2 + 110], COLORS = ['#ff3fa4', '#f5c518', '#36d6ff'], KEYS = [['ArrowLeft', 'KeyA', 'KeyJ'], ['ArrowDown', 'KeyS', 'KeyK', 'ArrowUp', 'KeyW'], ['ArrowRight', 'KeyD', 'KeyL']];
    const RATES = [1, 1.15, 1.3], GAP = 1.4, HIT_Y = H - 48, SPEED = 240;
    const s = { score: 0, combo: 0, best: 0, notes: [], verses: [], pops: [], held: [false, false, false], over: false, ready: false, startAt: 0, end: 0, bob: 0, hits: 0, misses: 0 };
    const sources = [];
    (async () => {
      await env.sound.loadSong();
      let at = env.sound.now() + 1.0;
      RATES.forEach((rate, v) => {
        SONG_NOTES.forEach(([t, d, lane, hold]) => {
          const l = v === 1 ? 2 - lane : v === 2 ? [1, 2, 0][lane] : lane;
          s.notes.push({ at: at + t / rate, dur: hold ? d / rate : 0, lane: l, verse: v, state: 'wait', syl: [at + t / rate, at + (t + d) / rate] });
        });
        s.verses.push({ at, rate });
        env.sound.song({ rate, when: at }).then((h) => h && sources.push(h));
        at += SONG_LENGTH / rate + GAP;
      });
      s.end = at - GAP + 0.8;
      s.ready = true;
    })();
    const now = () => env.sound.now() - env.sound.latency();
    const mult = () => (s.combo >= 20 ? 4 : s.combo >= 10 ? 3 : s.combo >= 5 ? 2 : 1);
    function judge(lane) {
      const t = now();
      const n = s.notes.filter((x) => x.lane === lane && x.state === 'wait' && Math.abs(x.at - t) <= 0.15).sort((a, b) => Math.abs(a.at - t) - Math.abs(b.at - t))[0];
      if (!n) return;
      const dt = Math.abs(n.at - t), perfect = dt <= 0.07;
      s.combo++; s.best = Math.max(s.best, s.combo); s.hits++;
      s.score += (perfect ? 100 : 50) * mult();
      n.state = n.dur ? 'holding' : 'hit';
      if (perfect) env.sound.fx.perfect(); else env.sound.fx.good();
      s.pops.push({ x: LANES[lane], y: HIT_Y - 30, txt: perfect ? 'PERFECT' : 'GOOD', c: perfect ? '#f5c518' : '#9be7ff', t: 0.6 });
    }
    return {
      get score() { return s.score; }, get over() { return s.over; },
      hud: () => [`VERSE ${Math.max(1, s.verses.filter((v) => v.at <= now()).length)}/3`, `COMBO ${s.combo}`],
      update() {
        if (!s.ready || s.over) return;
        const t = now();
        s.notes.forEach((n) => {
          if (n.state === 'wait' && t > n.at + 0.15) {
            n.state = 'miss'; s.combo = 0; s.misses++; env.sound.fx.miss();
            s.pops.push({ x: LANES[n.lane], y: HIT_Y - 30, txt: 'MISS', c: '#ff5470', t: 0.5 });
          }
          if (n.state === 'holding') {
            if (!s.held[n.lane]) { n.state = 'hit'; } // let go early: no hold bonus
            else if (t >= n.at + n.dur) { n.state = 'hit'; s.score += 200 * mult(); env.sound.fx.golden(); s.pops.push({ x: LANES[n.lane], y: HIT_Y - 50, txt: `HOLD +${200 * mult()}`, c: '#ffd700', t: 0.9 }); }
          }
        });
        s.bob = s.notes.some((n) => t >= n.syl[0] && t <= n.syl[1]) ? 1 : Math.max(0, s.bob - 0.08);
        s.pops.forEach((p) => { p.t -= 1 / 60; p.y -= 0.5; });
        s.pops = s.pops.filter((p) => p.t > 0);
        if (t > s.end) { s.over = true; env.sound.fx.fanfare(); env.onEnd(); }
      },
      render(g) {
        g.fillStyle = '#120a1f'; g.fillRect(0, 0, W, H);
        const t = now();
        const beam = g.createRadialGradient(W / 2, 30, 10, W / 2, 30, 200);
        beam.addColorStop(0, `rgba(255,220,150,${0.18 + s.bob * 0.15})`); beam.addColorStop(1, 'rgba(255,220,150,0)');
        g.fillStyle = beam; g.fillRect(0, 0, W, H);
        LANES.forEach((x, i) => {
          g.fillStyle = `${COLORS[i]}22`; g.fillRect(x - 34, 0, 68, H);
          g.strokeStyle = s.held[i] ? '#fff' : COLORS[i]; g.lineWidth = 3;
          g.beginPath(); g.arc(x, HIT_Y, 24, 0, Math.PI * 2); g.stroke();
          if (s.held[i]) { g.fillStyle = `${COLORS[i]}66`; g.fill(); }
        });
        drawOnkey(g, W / 2, 44, 96 + s.bob * 14, Math.sin(t * 6) * 0.05 * s.bob); // behind the notes, so none hide under him
        s.notes.forEach((n) => {
          if (n.state === 'hit' || n.state === 'miss') return;
          const y = HIT_Y - (n.at - t) * SPEED;
          if (y < -40 || y > H + 40) return;
          if (n.dur) {
            const y2 = HIT_Y - (n.at + n.dur - t) * SPEED;
            g.fillStyle = `${COLORS[n.lane]}88`; g.fillRect(LANES[n.lane] - 9, y2, 18, Math.max(0, (n.state === 'holding' ? HIT_Y : y) - y2));
          }
          if (n.state !== 'holding') { g.fillStyle = COLORS[n.lane]; g.beginPath(); g.arc(LANES[n.lane], y, 18, 0, Math.PI * 2); g.fill(); emoji(g, BANANA, LANES[n.lane], y + 1, 18); }
        });
        if (s.bob > 0.5) text(g, 'ook!', W / 2 + 70, 26, { size: 12, color: '#fff' });
        const v = s.verses.filter((x) => x.at <= t + 0.6).pop();
        if (v && t < v.at + 0.5) text(g, `${v.rate.toFixed(2)}x`, W / 2, 120, { size: 22, color: '#f5c518', align: 'center', glow: '#ff3fa4' });
        if (!s.ready) text(g, 'LOADING SONG…', W / 2, H / 2, { size: 16, align: 'center' });
        s.pops.forEach((p) => text(g, p.txt, p.x, p.y, { size: 12, color: p.c, align: 'center' }));
        if (mult() > 1) text(g, `x${mult()}`, W - 40, 62, { size: 16, color: '#f5c518', align: 'center' });
      },
      key(code, down) {
        const lane = KEYS.findIndex((ks) => ks.includes(code));
        if (lane < 0) return;
        if (down && !s.held[lane]) judge(lane);
        s.held[lane] = down;
      },
      tap(x, y, down) {
        const lane = x < W / 2 - 55 ? 0 : x > W / 2 + 55 ? 2 : 1;
        if (down) { judge(lane); s.held[lane] = true; } else s.held = [false, false, false];
      },
      stop() { sources.forEach((h) => h.stop()); },
    };
  };

  // Spike Dash: an endless runner. Jump (space / up / tap), double jump in the air, grab bananas, dodge spikes.
  GAMES.dash = (env) => {
    const GROUND = H - 40, X = 90;
    const s = { y: GROUND, vy: 0, jumps: 0, speed: 250, dist: 0, bananas: 0, score: 0, obs: [], fruit: [], next: 1.2, nextFruit: 0.8, over: false, t: 0, hills: 0 };
    const music = env.sound.music(140);
    function jump() {
      if (s.over || s.jumps >= 2) return;
      s.vy = s.jumps ? -560 : -640; env.sound.fx.jump(s.jumps === 1); s.jumps++;
    }
    return {
      get score() { return s.score; }, get over() { return s.over; },
      hud: () => [`${Math.floor(s.dist / 50)} m`, `${BANANA} ${s.bananas}`],
      update(dt) {
        if (s.over) return;
        s.t += dt;
        s.speed = Math.min(720, 250 + s.t * 11);
        music.tempo(140 + (s.speed - 250) / 12);
        s.dist += s.speed * dt;
        s.hills += s.speed * dt * 0.25;
        s.vy += 1900 * dt; s.y += s.vy * dt;
        if (s.y >= GROUND) { s.y = GROUND; s.vy = 0; s.jumps = 0; }
        s.next -= dt; s.nextFruit -= dt;
        if (s.next <= 0) {
          const k = Math.min(1, s.t / 60), r = Math.random();
          const n = r < 0.18 + k * 0.25 ? 2 : 1, tall = r > 0.85 - k * 0.1;
          for (let i = 0; i < n; i++) s.obs.push({ x: W + 20 + i * 30, h: tall && i === 0 ? 52 : 30 });
          s.next = Math.max(0.55, 1.35 - k * 0.6) * (0.75 + Math.random() * 0.6);
        }
        if (s.nextFruit <= 0) { s.fruit.push({ x: W + 20, y: GROUND - 60 - Math.random() * 90 }); s.nextFruit = 0.9 + Math.random() * 1.2; }
        s.obs.forEach((o) => { o.x -= s.speed * dt; });
        s.fruit.forEach((f) => { f.x -= s.speed * dt; });
        s.obs = s.obs.filter((o) => o.x > -40);
        const top = s.y - 30;
        s.fruit = s.fruit.filter((f) => {
          if (Math.abs(f.x - X) < 30 && f.y > top - 14 && f.y < s.y + 10) { s.bananas++; env.sound.fx.pickup(); return false; }
          return f.x > -30;
        });
        if (s.obs.some((o) => Math.abs(o.x - X) < 22 && s.y > GROUND - o.h + 6)) {
          s.over = true; music.stop(); env.sound.fx.hurt(); setTimeout(() => env.sound.fx.gameOver(), 250); env.onEnd();
        }
        s.score = Math.floor(s.dist / 10) + s.bananas * 50;
      },
      render(g) {
        const sky = g.createLinearGradient(0, 0, 0, H);
        sky.addColorStop(0, '#2a0845'); sky.addColorStop(0.55, '#ff6f61'); sky.addColorStop(1, '#ffb347');
        g.fillStyle = sky; g.fillRect(0, 0, W, H);
        g.fillStyle = '#ffd36e'; g.beginPath(); g.arc(W * 0.72, GROUND - 60, 46, 0, Math.PI * 2); g.fill();
        g.fillStyle = '#5a1e3d';
        for (let i = -1; i < 6; i++) { const x = i * 120 - (s.hills % 120); g.beginPath(); g.moveTo(x, GROUND); g.quadraticCurveTo(x + 60, GROUND - 70, x + 120, GROUND); g.fill(); }
        g.fillStyle = '#1b0f1f'; g.fillRect(0, GROUND, W, H - GROUND);
        g.fillStyle = '#ff3fa4'; for (let x = -(s.dist % 40); x < W; x += 40) g.fillRect(x, GROUND + 6, 20, 3);
        s.fruit.forEach((f) => emoji(g, BANANA, f.x, f.y, 20));
        s.obs.forEach((o) => { // a planted spike: a dark cylinder with a blinking red light
          g.fillStyle = '#2d2d33'; g.fillRect(o.x - 10, GROUND - o.h, 20, o.h);
          g.fillStyle = '#4a4a55'; g.fillRect(o.x - 12, GROUND - o.h - 4, 24, 6);
          g.fillStyle = (Math.floor(s.t * 6) % 2) ? '#ff4655' : '#7a1f28'; g.fillRect(o.x - 3, GROUND - o.h + 8, 6, 6);
          if (o.h > 40) { g.fillStyle = '#ff4655'; g.fillRect(o.x - 3, GROUND - o.h + 24, 6, 6); }
        });
        drawOnkey(g, X, s.y - 20, 78, s.vy * 0.0006);
        if (s.t < 3) text(g, 'SPACE / TAP TO JUMP · TWICE FOR A DOUBLE', W / 2, 60, { size: 12, align: 'center' });
      },
      key(code, down) { if (down && ['Space', 'ArrowUp', 'KeyW'].includes(code)) jump(); },
      tap(x, y, down) { if (down) jump(); },
      stop() { music.stop(); },
    };
  };

  // ---- the machine window: pay, count down, play, send the score -------------------------------
  let run = null; // the game being played: { key, token, game, raf, ... }
  async function insertCoin(key, btn) {
    if (run) return;
    Sound.ctx(); // unlock audio inside the click
    Sound.fx.coin();
    btn?.classList.add('coin-drop');
    try {
      const r = await api('/api/arcade/start', { method: 'POST', body: JSON.stringify({ game: key }) });
      if (state.me) state.me.bananas = r.wallet;
      open(key, r.token);
      loadMe && loadMe();
    } catch (e) {
      toast(e.message, 'bad');
    } finally {
      setTimeout(() => btn?.classList.remove('coin-drop'), 700);
    }
  }

  function open(key, token) {
    const info = (state.arcade?.games || []).find((x) => x.key === key) || { name: key };
    const box = document.createElement('div');
    box.id = 'arcade-modal';
    box.className = 'arcade-modal';
    box.innerHTML = `<div class="arcade-cab" role="dialog" aria-modal="true" aria-label="${esc(info.name)}">
      <div class="cab-marquee big"><span>${esc(info.icon || '')}</span>${esc(info.name)}</div>
      <div class="cab-screen"><canvas width="${W}" height="${H}" id="arcade-canvas" tabindex="0" aria-label="${esc(info.name)} game screen"></canvas><div class="crt"></div></div>
      <div class="cab-controls"><span class="muted small">${esc(CONTROLS[key])}</span>
        <span class="btn-row"><button class="btn ghost small" id="arcade-mute">${soundText()}</button>
        <button class="btn ghost small" id="arcade-quit">Quit</button></span></div></div>`;
    document.body.append(box);
    document.body.classList.add('modal-open');
    const canvas = $('#arcade-canvas', box), g = canvas.getContext('2d');
    g.imageSmoothingEnabled = false;
    canvas.focus();
    run = { key, token, info, box, canvas, g, phase: 'count', count: 3.5, game: null, last: performance.now(), paused: false, result: null };
    const env = { sound: Sound, onEnd: () => finish() };
    const onKey = (e) => {
      if (!run) return;
      if (e.key === 'Escape') { if (e.type === 'keydown') quit(); return; }
      if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Space'].includes(e.code)) e.preventDefault();
      if (run.paused && e.type === 'keydown') { resume(); return; }
      if (run.phase === 'play' && run.game && !e.repeat) run.game.key(e.code, e.type === 'keydown');
      // Only Enter replays (Space is a jump key), and not in the first second, so a late press can't pay again by accident.
      if (run.phase === 'done' && e.type === 'keydown' && e.code === 'Enter' && performance.now() - run.doneAt > 1000) again();
    };
    const toLogical = (e) => { const r = canvas.getBoundingClientRect(); return [(e.clientX - r.left) * (W / r.width), (e.clientY - r.top) * (H / r.height)]; };
    const onPointer = (e) => {
      if (!run) return;
      if (run.paused) { resume(); return; }
      if (run.phase !== 'play' || !run.game) return;
      const [x, y] = toLogical(e), down = e.type === 'pointerdown' || (e.type === 'pointermove' && e.buttons);
      if (e.type === 'pointermove' && !e.buttons) return;
      run.game.tap(x, y, !!down);
    };
    const onHide = () => { if (document.hidden && run && run.phase === 'play') { run.paused = true; Sound.pause(); } };
    window.addEventListener('keydown', onKey); window.addEventListener('keyup', onKey);
    ['pointerdown', 'pointermove', 'pointerup', 'pointercancel'].forEach((t) => canvas.addEventListener(t, onPointer));
    document.addEventListener('visibilitychange', onHide);
    run.cleanup = () => {
      window.removeEventListener('keydown', onKey); window.removeEventListener('keyup', onKey);
      document.removeEventListener('visibilitychange', onHide);
    };
    $('#arcade-mute', box).addEventListener('click', (e) => { Sound.toggleMute(); e.currentTarget.innerHTML = soundText(); canvas.focus(); });
    $('#arcade-quit', box).addEventListener('click', quit);
    const resume = () => { run.paused = false; run.last = performance.now(); Sound.resume(); };
    let lastBeep = 4;
    const frame = (now) => {
      if (!run) return;
      const dt = Math.min(0.05, (now - run.last) / 1000);
      run.last = now;
      if (!run.paused) {
        if (run.phase === 'count') {
          run.count -= dt;
          const n = Math.ceil(run.count - 0.5);
          if (n < lastBeep && n >= 0) { Sound.fx.count(n === 0); lastBeep = n; }
          if (run.count <= 0.5) { run.phase = 'play'; run.game = GAMES[key](env); }
        } else if (run.phase === 'play') run.game.update(dt);
      }
      paint();
      run.raf = requestAnimationFrame(frame);
    };
    Sound.fx.start();
    run.raf = requestAnimationFrame(frame);
  }

  const CONTROLS = {
    catch: '← → or A D to move (or drag on the screen). Esc quits.',
    says: '← ↓ → or A S D (or tap the lanes) as each banana hits the ring; hold the long one. Esc quits.',
    dash: 'Space or ↑ (or tap) to jump; press again in the air to double jump. Esc quits.',
  };

  function paint() {
    const { g, game, phase } = run;
    if (game) game.render(g);
    else { g.fillStyle = '#0b0b12'; g.fillRect(0, 0, W, H); drawOnkey(g, W / 2, H / 2 - 30, 150); }
    // HUD: score left, the game's two stats right
    if (game) {
      const [a, b] = game.hud();
      text(g, `SCORE ${String(game.score).padStart(6, '0')}`, 12, 16, { size: 14, color: '#fff' });
      text(g, a, W - 12, 16, { size: 13, color: '#f5c518', align: 'right' });
      text(g, b, W - 12, 34, { size: 13, color: '#ff8fb8', align: 'right' });
    }
    if (phase === 'count') {
      const n = Math.ceil(run.count - 0.5);
      text(g, n > 0 ? String(n) : 'GO!', W / 2, H / 2 + 60, { size: 40, color: '#f5c518', align: 'center', glow: '#ff3fa4' });
      text(g, run.info.name.toUpperCase(), W / 2, 36, { size: 18, color: '#fff', align: 'center', glow: '#36d6ff' });
    }
    if (run.paused) { g.fillStyle = 'rgba(0,0,0,.6)'; g.fillRect(0, 0, W, H); text(g, 'PAUSED · PRESS ANY KEY', W / 2, H / 2, { size: 16, align: 'center' }); }
    if (phase === 'done') {
      g.fillStyle = 'rgba(0,0,0,.72)'; g.fillRect(0, 0, W, H);
      const r = run.result;
      text(g, 'GAME OVER', W / 2, 56, { size: 30, color: '#ff3fa4', align: 'center', glow: '#ff3fa4' });
      text(g, `SCORE ${game.score}`, W / 2, 104, { size: 20, color: '#fff', align: 'center' });
      if (!r) text(g, 'SAVING…', W / 2, 150, { size: 14, align: 'center' });
      else if (r.error) text(g, r.error.toUpperCase(), W / 2, 150, { size: 12, color: '#ff8fb8', align: 'center' });
      else {
        if (r.new_best && (Math.floor(performance.now() / 350) % 2)) text(g, r.champion ? '★ NEW #1 HIGH SCORE ★' : '★ NEW PERSONAL BEST ★', W / 2, 146, { size: 16, color: '#f5c518', align: 'center', glow: '#f5c518' });
        text(g, r.rank ? `RANK #${r.rank} ON THE BOARD` : `YOUR BEST ${r.best_before ?? 0}`, W / 2, 176, { size: 13, color: '#9be7ff', align: 'center' });
        (r.board || []).slice(0, 3).forEach((b, i) => text(g, `${i + 1}. ${b.bettor.slice(0, 14).padEnd(14, ' ')} ${String(b.score).padStart(7, ' ')}`, W / 2, 210 + i * 20, { size: 13, color: i === 0 ? '#ffd700' : '#fff', align: 'center' }));
      }
      text(g, `ENTER: AGAIN (5 ${BANANA})  ·  ESC: LEAVE`, W / 2, H - 22, { size: 12, color: '#f5c518', align: 'center' });
    }
  }

  async function finish() {
    const r = run; // the window may be closed while the score is saving
    if (!r || r.phase === 'done') return;
    r.phase = 'done';
    r.doneAt = performance.now();
    const score = r.game ? r.game.score : 0;
    try {
      r.result = await api('/api/arcade/finish', { method: 'POST', body: JSON.stringify({ token: r.token, score }) });
      // What Onkey says when you leave the machine: the best moment of the visit (a #1, a new best, else the last game).
      const now = { game: r.info.name, score, best: !!r.result.new_best, champion: !!r.result.champion };
      const rank = (t) => (t ? (t.champion ? 3 : t.best ? 2 : 1) : 0);
      if (rank(now) >= rank(tell)) tell = now;
      // The reward: a new personal best gets the fanfare, then Onkey sings his song, whole.
      if (r.result.new_best && score > 0) setTimeout(() => { Sound.fx.fanfare(); setTimeout(() => Sound.song({ vol: 1 }), 700); }, 900);
    } catch (e) {
      r.result = { error: e.message };
    }
    load().catch(() => {});
  }

  let quitting = false;
  async function quit() {
    if (!run || quitting) return;
    quitting = true;
    try {
      if (run.phase === 'play') await finish(); // walking away ends the game: the score so far still counts
      close();
    } finally { quitting = false; }
  }

  // The visit's best game for Onkey (finish()), told when you leave the machine, not between replays.
  let tell = null;
  function close(replaying = false) {
    if (!run) return;
    if (!replaying && tell) { window.FiveOnkey?.note('arcade', tell); tell = null; }
    cancelAnimationFrame(run.raf);
    run.game?.stop();
    run.cleanup();
    run.box.remove();
    document.body.classList.remove('modal-open');
    run = null;
    Sound.resume();
    load().then(() => { if (state.view === 'arcade') draw(); }).catch(() => {});
  }

  async function again() {
    const key = run.key;
    close(true);
    await insertCoin(key, null);
  }

  // ---- the Arcade tab ------------------------------------------------------------------
  function viewArcade() {
    const a = state.arcade;
    if (!a) return '<div class="loading">Loading…</div>';
    const me = a.me;
    const medal = (i) => ['🥇', '🥈', '🥉'][i] || String(i + 1);
    const cabinets = a.games.map((g) => {
      const top = g.board[0];
      const coin = me ? `<button class="coin-slot" data-game="${g.key}" ${me.wallet + 1e-9 < a.price ? 'disabled title="Not enough bananas"' : ''}>
          <span class="coin" aria-hidden="true">${BANANA}</span><span>INSERT ${a.price} ${BANANA}</span></button>`
        : '<a class="coin-slot" href="#odds"><span>SIGN IN TO PLAY</span></a>';
      return `<div class="cabinet cab-${g.key}">
        <div class="cab-marquee"><span aria-hidden="true">${g.icon}</span>${esc(g.name)}</div>
        <div class="cab-screen attract"><div class="attract-art" aria-hidden="true">${g.icon}</div><div class="attract-text">INSERT COIN</div><div class="crt"></div></div>
        <div class="cab-body">
          <p class="small">${esc(g.desc)}</p>
          <div class="cab-stats"><div><span class="muted small">Champion</span><b>${top ? `👑 ${nameHtml(top.bettor, { link: true })} · ${fmt.n0(top.score)}` : 'Nobody yet'}</b></div>
            <div><span class="muted small">Your best</span><b>${me ? (g.my_best != null ? fmt.n0(g.my_best) : 'Not played') : '–'}</b></div></div>
          ${coin}
          <div class="muted small cab-plays">${fmt.n0(g.plays)} play${g.plays === 1 ? '' : 's'} so far</div>
        </div></div>`;
    }).join('');
    const boards = a.games.map((g) => `<div class="hs-board"><h3>${g.icon} ${esc(g.name)}</h3>${g.board.length
      ? `<ol class="hs-list">${g.board.map((r, i) => `<li class="${me && r.bettor.toLowerCase() === me.name.toLowerCase() ? 'me' : ''}"><span class="hs-rank">${medal(i)}</span>` +
        `<span class="hs-name">${nameHtml(r.bettor, { link: true })}</span><span class="hs-score">${fmt.n0(r.score)}</span></li>`).join('')}</ol>`
      : '<p class="muted small">No scores yet. Be the first.</p>'}</div>`).join('');
    return `<section class="arcade-sign"><div class="neon">ONKEY'S ARCADE</div>
        <div class="arcade-sub">${a.price} ${BANANA} a play · the only prize is glory</div>
        <div class="btn-row arcade-tools">${me ? `<span class="arcade-wallet">${fmt.n1(me.wallet)} ${BANANA} to spend</span>` : ''}
          <button class="btn ghost small" id="jukebox">🎵 Play Onkey's song</button>
          <button class="btn ghost small" id="arcade-sound">${soundText()}</button></div></section>
      <div class="arcade-row">${cabinets}</div>
      <section class="card"><div class="section-head"><h2>High scores</h2><span class="muted small">Each player's best, all time</span></div>
        <div class="hs-grid">${boards}</div></section>`;
  }

  let jukebox = null;
  function bind(view) {
    $$('.coin-slot[data-game]', view).forEach((b) => b.addEventListener('click', () => insertCoin(b.dataset.game, b)));
    $('#jukebox', view)?.addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      if (jukebox) { jukebox.stop(); jukebox = null; btn.textContent = "🎵 Play Onkey's song"; return; }
      Sound.ctx();
      jukebox = await Sound.song();
      if (!jukebox) { toast("Couldn't play the song in this browser", 'bad'); return; }
      btn.textContent = '⏹ Stop';
      jukebox.source.onended = () => { jukebox = null; if (btn.isConnected) btn.textContent = "🎵 Play Onkey's song"; };
    });
    $('#arcade-sound', view)?.addEventListener('click', (e) => { Sound.toggleMute(); e.currentTarget.innerHTML = soundText(); });
  }

  return { init, load, viewArcade, bind };
})();
