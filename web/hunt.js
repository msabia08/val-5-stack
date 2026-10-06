/* 5-Stack Tracker: the Banana Hunt (Casino › Banana Hunt; data from /api/hunt, picks from /api/hunt/click, both in
 * fivestack/hunt.py).
 *
 * Onkey dropped his bananas all over the field. The server puts one down, the page draws it, and every click that
 * lands on it pays and moves it somewhere else (the server judges each click and picks each spot, so the page only
 * reports where you clicked). Misses leave it where it is; picks faster than the server pays aren't paid; there's a
 * daily cap in credits that turns over at midnight Pacific, like the daily wheel, which doesn't apply while you're
 * under the floor (50 credits). Onkey, in his corner, winds up and throws every banana in along an arc (`throwTo()`,
 * the Web Animations API, THROW_MS). The whole page is only redrawn when the hunt closes for the day; everything else
 * is updated in place so the hunt stays snappy.
 *
 * What makes it a game (hunt.py decides all of it; the page draws it and runs the timers):
 * - a banana can be caught in the air for double (a click on it mid-flight is sent with `air`, how far along it was),
 *   except a volley's steel bananas, which can only be picked once they're down;
 * - a golden banana pays more but rots a few seconds after it lands; a bunch is five at once with a timer and a bonus
 *   for sweeping them; a rotten decoy next to the real one freezes you; Greg walks in to take a banana unless you pick
 *   it first or click him away. When one of those timers runs out the page asks /api/hunt/next what happened;
 * - two aren't picked with a click at all: a green banana ripens while you hold the button down on it (`startHold()`),
 *   and a vine banana is dragged along its vine to the far end (`startDrag()`); letting go early costs nothing;
 * - Onkey's bongos: two drums, each with a meter that only quick tapping fills (`tapDrum()`);
 * - picks in a row build a combo (x2, x3) shown in the corner of the field; a miss breaks it;
 * - everything has a sound (`sfx`, synthesised; the speaker button in the field's corner mutes it, `fs.huntMuted`);
 * - from Onkey's lore: the scientist's boss fight (`bossWave()`: his claws come for Onkey in waves), and Man Strudel visits
 *   (`visitStrudel()`), who takes nothing;
 * - one pick a day hides an item, and the field's scenery changes by the day (`hunt.theme`);
 * - once the day's credits are picked the hunt closes, with a Keep playing button: the same game for nothing
 *   (`freePlay`: every request then carries `free`, and the server pays, takes and records nothing).
 *
 * FiveHunt.init(ctx) gets app.js's helpers; load(), view() and bind() are called like the other pages'.
 * Plain JS, no dependencies; loaded before app.js.
 */
window.FiveHunt = (() => {
  'use strict';

  let state, $, $$, api, draw, esc, fmt, kpi, renderMe, toast, plainName;
  let data = null, owner = undefined;
  let target = null; // what's on the field: the server's target ({id, kind, x, y, items, decoy, greg}), or null
  let busy = false; // a click is on its way to the server
  let flight = null; // Onkey's throw in the air: {t, anim, catchable}
  let frozenUntil = 0; // a rotten banana: no picking until then (performance.now())
  let timers = []; // the timeouts and animations that belong to the target on the field
  let session = 0; // bananas picked since the page was opened
  let hop = { id: 0, i: 0 }; // a bouncing banana: which of its spots it's on (land() moves it along)
  let comboTimer = 0; // the combo lapses on the server after a while without a pick: the page drops it then too
  let grip = null; // a banana being held or dragged: {kind: 'hold' / 'drag', el, fieldEl, pointer (null: the keyboard), ...}
  let freePlay = false; // "Keep playing" was pressed: the day's credits are picked and the hunt goes on for nothing
  let greenDown = 0; // when the green banana on the field landed (performance.now()): it has been going brown since
  const THROW_MS = 750, THROW_DELAY = 120; // how long a banana is in the air, after Onkey's wind-up
  const how = (summary, more) => `<details class="how"><summary>${summary}</summary><div class="how-body">${more}</div></details>`;
  // The field of the day: fixed scenery per theme, so the field looks the same all day.
  const SCENERY = {
    jungle: [['🌴', 6, 14], ['🌿', 22, 88], ['🌴', 58, 10], ['🪨', 40, 92], ['🌿', 82, 20], ['🌴', 93, 84], ['🌱', 50, 50], ['🍃', 70, 62]],
    night: [['🌙', 88, 12], ['🌴', 6, 16], ['🌴', 60, 12], ['🪨', 40, 92], ['✨', 24, 30], ['✨', 72, 58], ['✨', 46, 72], ['✨', 14, 66], ['🦉', 92, 80]],
    rain: [['🌧️', 14, 10], ['🌧️', 52, 8], ['🌧️', 84, 12], ['🌴', 6, 60], ['🌿', 30, 90], ['🍄', 66, 84], ['🐸', 46, 52], ['🌿', 90, 40]],
    beach: [['🌴', 6, 14], ['🌴', 92, 16], ['🐚', 26, 84], ['🦀', 60, 70], ['⛱️', 44, 20], ['🌊', 76, 92], ['🌊', 16, 94], ['⭐', 82, 56]],
    ruins: [['🗿', 8, 18], ['🏛️', 56, 12], ['🪨', 30, 88], ['🪨', 78, 30], ['🌿', 90, 82], ['🏺', 44, 54], ['🌴', 94, 12], ['🕸️', 18, 56]],
  };
  const THEME_NAME = { jungle: 'the jungle', night: 'the jungle at night', rain: 'the rains', beach: 'the beach', ruins: 'the old ruins' };

  function init(ctx) {
    ({ state, $, $$, api, draw, esc, fmt, kpi, renderMe, toast, plainName } = ctx);
    window.addEventListener('resize', refit);
  }

  // On a phone the server's field (1200 wide, 600 tall) doesn't fit, so the page draws it upright: the server's x
  // runs down the screen and its y across, each scaled to a field as wide as the screen and about a screen tall.
  // `at()` is where a field point is drawn. A tap is judged in screen pixels (within TAP_R of the thing as drawn,
  // about a fingertip) and reported to the server as that thing's own spot, as picking it with the keyboard is; a
  // tap anywhere else is reported where it fell, which the server calls a miss. The desktop draws the field as it is.
  const TAP_R = 30;
  const SHOW_BOARD = false; // the Top pickers card under the field: set to true to bring it back
  const onPhone = () => matchMedia('(max-width: 640px)').matches;
  // On a desktop the field is drawn as big as the window allows: the server's 1200 x 600 scaled as a whole by `k` (a
  // CSS transform on .hunt-zoom, so everything inside is still placed and sized in the server's pixels, and a click is
  // scaled back before it's judged). What the page takes up around the field, in CSS pixels:
  const FIT = { page: 32, side: 296, card: 34, above: 92, below: 52, min: 0.6, max: 1.6 };
  function fit(f) {
    const w = document.documentElement.clientWidth - FIT.page - FIT.side - FIT.card;
    const k = Math.max(FIT.min, Math.min(FIT.max, w / f.w, (window.innerHeight - FIT.above - FIT.below) / f.h));
    return Math.floor(k * f.w) / f.w; // a whole number of pixels wide
  }
  function layout() {
    const f = data.hunt.field;
    if (!onPhone()) return { phone: false, w: f.w, h: f.h, k: fit(f), at: (t) => ({ x: t.x, y: t.y }), toField: (x, y) => ({ x, y }) };
    const w = Math.min(f.h, document.documentElement.clientWidth - 42); // the page's and the card's padding either side
    const h = Math.min(f.w, Math.max(320, window.innerHeight - 130)); // under the top bar, with the live line below
    const kx = w / f.h, ky = h / f.w;
    return { phone: true, w, h, k: 1, at: (t) => ({ x: t.y * kx, y: t.x * ky }), toField: (x, y) => ({ x: y / ky, y: x / kx }) };
  }
  // The field in its frame: .hunt-fit takes the room the scaled field needs, .hunt-zoom scales it.
  const fitStyle = (lay) => `width:${Math.round(lay.w * lay.k)}px;height:${Math.round(lay.h * lay.k)}px`;
  const zoomStyle = (lay) => `width:${lay.w}px;height:${lay.h}px${lay.k !== 1 ? `;transform:scale(${lay.k})` : ''}`;
  function refit() { // the window changed size: the field follows, without being drawn again
    const frame = $('.hunt-fit'), zoom = $('.hunt-zoom');
    if (!data || state.view !== 'hunt' || !frame || !zoom) return;
    const lay = layout();
    frame.style.cssText = fitStyle(lay);
    zoom.style.cssText = zoomStyle(lay);
    const fieldEl = $('.hunt-field', zoom);
    if (fieldEl) { fieldEl.style.width = `${lay.w}px`; fieldEl.style.height = `${lay.h}px`; }
  }

  // A request to the hunt. While playing on past the day's credits, each one says so (`free`).
  const post = (path, body = {}) => api(path, { method: 'POST', body: JSON.stringify(freePlay ? { ...body, free: true } : body) });

  async function load() {
    const name = state.me?.name || null;
    const next = await api('/api/hunt');
    if (name !== owner) { session = 0; target = null; freePlay = false; }
    owner = name;
    data = next;
    if (next.me && (!next.me.done || freePlay)) { // have the server put one down (or start the timers of the one that's there again)
      const r = await post('/api/hunt/start');
      data.me = r.me;
    }
    target = data.me ? data.me.target : null;
  }

  // ---- sound --------------------------------------------------------------------------------------------------------
  // Everything is synthesised, as the daily wheel's sounds are: tones through a gain envelope, and filtered noise for
  // whooshes, thuds and cracks. Nothing plays until the page has been clicked or a key pressed (browsers won't start
  // sound before that), and the speaker button in the field's corner mutes it (`fs.huntMuted`).
  let audio = null, muted = false, noiseBuf = null, humNode = null, lastZip = 0;
  try { muted = localStorage.getItem('fs.huntMuted') === '1'; } catch (e) { /* no storage: sound on */ }
  function ctx() {
    if (muted) return null;
    if (!audio && navigator.userActivation && !navigator.userActivation.hasBeenActive) return null;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      if (audio.state === 'suspended') audio.resume();
    } catch (e) { audio = null; }
    return audio;
  }
  function tone(freq, start, dur, { type = 'sine', vol = 0.12, to = null, attack = 0.005, glide = null } = {}) { // glide: how long the pitch takes to reach `to` (default: all of dur)
    const a = ctx(); if (!a) return;
    const t = a.currentTime + start, o = a.createOscillator(), g = a.createGain();
    o.type = type; o.frequency.setValueAtTime(freq, t);
    if (to) o.frequency.exponentialRampToValueAtTime(to, t + (glide || dur));
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(vol, t + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g).connect(a.destination); o.start(t); o.stop(t + dur + 0.05);
  }
  function noise(start, dur, { freq = 2000, q = 1, vol = 0.15, type = 'bandpass', to = null } = {}) {
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
  const notes = (list, gap, dur, opts) => list.forEach((f, i) => tone(f, i * gap, dur, opts)); // a run of notes, one after another
  const sfx = {
    throw: (at = 0) => noise(at, 0.3, { freq: 450, to: 1900, q: 1.2, vol: 0.06 }), // Onkey's throw: a whoosh
    land: () => { tone(150, 0, 0.09, { vol: 0.14, to: 70 }); noise(0, 0.05, { freq: 900, vol: 0.05 }); },
    // A pick: two quick notes, a little higher with every pick in the combo.
    pick: (combo) => { const f = 494 * 2 ** (Math.min(combo, 24) / 24); tone(f, 0, 0.07, { type: 'triangle', vol: 0.15 }); tone(f * 1.5, 0.06, 0.11, { type: 'triangle', vol: 0.13 }); },
    caught: () => { tone(1319, 0, 0.06, { type: 'triangle', vol: 0.13 }); tone(1760, 0.05, 0.08, { type: 'triangle', vol: 0.12 }); tone(2637, 0.1, 0.14, { vol: 0.07 }); },
    gold: () => notes([1047, 1319, 1568, 2093], 0.055, 0.18, { type: 'triangle', vol: 0.11 }),
    bonus: () => notes([784, 988, 1175, 1568], 0.07, 0.14, { type: 'square', vol: 0.045 }), // a bunch or a volley swept
    comboUp: () => notes([659, 880, 1319], 0.08, 0.22, { type: 'sawtooth', vol: 0.045 }),
    comboLost: () => tone(330, 0.05, 0.32, { type: 'sawtooth', vol: 0.05, to: 110 }),
    miss: () => tone(170, 0, 0.08, { type: 'square', vol: 0.035, to: 120 }),
    yuck: () => { tone(120, 0, 0.36, { type: 'sawtooth', vol: 0.08, to: 60 }); noise(0, 0.25, { freq: 300, q: 2, vol: 0.07 }); },
    corn: () => { tone(294, 0, 0.16, { type: 'square', vol: 0.05 }); tone(208, 0.16, 0.32, { type: 'square', vol: 0.05, to: 175 }); },
    crack: () => { noise(0, 0.08, { freq: 5200, type: 'highpass', vol: 0.28 }); [2400, 3100, 3900, 2900, 3500].forEach((f, i) => tone(f, 0.03 + i * 0.035, 0.09, { vol: 0.045 })); },
    split: () => { noise(0, 0.06, { freq: 1800, vol: 0.16 }); tone(700, 0, 0.09, { type: 'triangle', vol: 0.1, to: 1150 }); },
    boing: () => tone(220, 0, 0.16, { vol: 0.1, to: 520 }),
    // Steel: a struck ring (a few partials that aren't in tune with each other), louder when it's you that hit it.
    clang: (vol = 1) => { noise(0, 0.03, { freq: 4200, vol: 0.14 * vol }); [523, 1245, 1870, 2794].forEach((f, i) => tone(f, 0, 0.5 - i * 0.09, { type: i ? 'sine' : 'triangle', vol: (0.08 - i * 0.014) * vol })); },
    rot: () => tone(262, 0, 0.4, { type: 'triangle', vol: 0.07, to: 131 }),
    greg: () => { tone(196, 0, 0.1, { type: 'square', vol: 0.045 }); tone(147, 0.12, 0.18, { type: 'square', vol: 0.045 }); },
    stolen: () => notes([392, 311, 233], 0.1, 0.2, { type: 'square', vol: 0.05 }),
    shoo: () => noise(0, 0.22, { freq: 800, to: 3200, q: 1.5, vol: 0.13 }),
    ripe: () => { tone(880, 0, 0.1, { type: 'triangle', vol: 0.13 }); tone(1319, 0.08, 0.24, { type: 'triangle', vol: 0.13 }); },
    // Along the vine: a tick for the spots as they pass, rising toward the far end (never more than one every 35 ms).
    zip: (k) => { const now = performance.now(); if (now - lastZip < 35) return; lastZip = now; tone(480 + 760 * k, 0, 0.045, { type: 'triangle', vol: 0.06 }); },
    vine: () => notes([659, 831, 988, 1319], 0.055, 0.16, { type: 'triangle', vol: 0.11 }),
    slip: () => tone(500, 0, 0.15, { type: 'triangle', vol: 0.07, to: 240 }),
    alarm: () => [0, 0.32, 0.64].forEach((at) => tone(440, at, 0.24, { type: 'sawtooth', vol: 0.06, to: 660 })), // the scientist
    wave: () => tone(330, 0, 0.26, { type: 'sawtooth', vol: 0.055, to: 495 }),
    claw: () => { noise(0, 0.05, { freq: 2500, q: 2, vol: 0.18 }); tone(180, 0, 0.1, { type: 'square', vol: 0.06, to: 90 }); },
    waveClear: () => notes([523, 659, 784], 0.07, 0.15, { type: 'triangle', vol: 0.1 }),
    bossWon: () => notes([523, 659, 784, 1047, 1319], 0.1, 0.32, { type: 'triangle', vol: 0.11 }),
    bossLost: () => { tone(220, 0, 0.5, { type: 'sawtooth', vol: 0.07, to: 70 }); tone(110, 0.1, 0.7, { vol: 0.11, to: 50 }); },
    bongo: (d) => { // a bongo: a slap of the hand on the skin, then a short round knock that drops a little in pitch; the big drum is the low one
      const f = d ? 215 : 320;
      tone(f * 1.45, 0, 0.2, { vol: 0.34, to: f, glide: 0.035, attack: 0.002 });
      tone(f * 2.4, 0, 0.07, { type: 'triangle', vol: 0.07, to: f * 2.05, glide: 0.03, attack: 0.002 });
      noise(0, 0.022, { freq: d ? 1500 : 2300, q: 1.4, vol: 0.24 });
    },
    drumFull: () => notes([784, 1175], 0.06, 0.15, { type: 'triangle', vol: 0.1 }),
    bongos: () => [[0, 0], [90, 0], [180, 1], [300, 0], [420, 1]].forEach(([ms, d]) => setTimeout(() => sfx.bongo(d), ms)), // both full: a little fill
    strudel: () => [0, 0.3, 0.6].forEach((at) => tone(82, at, 0.16, { vol: 0.16, to: 50 })), // heavy feet
    found: () => notes([1319, 1568, 2093, 2637], 0.06, 0.26, { vol: 0.08 }),
    done: () => notes([784, 659, 523, 392], 0.14, 0.32, { type: 'triangle', vol: 0.09 }),
    // While a green banana is held: a hum that climbs for `secs`. hum(0) stops it.
    hum: (secs) => {
      if (humNode) {
        const { o, g } = humNode, t = audio.currentTime;
        humNode = null;
        try { g.gain.cancelScheduledValues(t); g.gain.setTargetAtTime(0.0001, t, 0.02); o.stop(t + 0.12); } catch (e) { /* it had stopped */ }
      }
      const a = secs ? ctx() : null;
      if (!a) return;
      const o = a.createOscillator(), g = a.createGain(), t = a.currentTime;
      o.type = 'triangle'; o.frequency.setValueAtTime(262, t); o.frequency.exponentialRampToValueAtTime(880, t + secs);
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.06, t + 0.06);
      o.connect(g).connect(a.destination); o.start(t); o.stop(t + secs + 0.4);
      humNode = { o, g };
    },
  };
  // What the server's answer to a click or a timer sounds like.
  function sound(r, lost, wasMult) {
    if (r.hit) {
      if (r.kind === 'boss') sfx.bossWon(); else if (r.kind === 'golden') sfx.gold(); else if (r.air) sfx.caught();
      else if (r.kind === 'green') sfx.ripe(); else if (r.kind === 'vine') sfx.vine(); else if (r.kind === 'bongos') sfx.bongos(); else sfx.pick(r.combo || 0);
      if (r.swept && r.bunch_bonus) sfx.bonus();
      if (r.found) sfx.found();
      if ((r.mult || 1) > wasMult) sfx.comboUp();
    } else {
      const name = { rotten: 'yuck', corn: 'corn', cracked: 'crack', split: 'split', rotted: 'rot', corn_gone: 'rot', bunch_over: 'rot', stolen: 'stolen',
        shooed: 'shoo', unripe: 'slip', vine: 'slip', slipped: 'slip', offbeat: 'slip', miss: 'miss' }[r.reason];
      if (name) sfx[name]();
      if (lost && r.reason === 'miss') sfx.comboLost();
    }
    if (r.done) sfx.done();
  }
  const speaker = () => `<button type="button" class="hunt-sound" id="hunt-sound" aria-pressed="${!muted}" aria-label="${muted ? 'Turn the hunt\'s sound on' : 'Turn the hunt\'s sound off'}" ` +
    `title="${muted ? 'Sound off' : 'Sound on'}">${window.speakerIcon(muted, 18)}</button>`;
  function bindSound(fieldEl) {
    $('#hunt-sound', fieldEl)?.addEventListener('click', () => {
      muted = !muted;
      try { localStorage.setItem('fs.huntMuted', muted ? '1' : '0'); } catch (e) { /* not remembered */ }
      if (muted) sfx.hum(0);
      const b = $('#hunt-sound', fieldEl); if (b) b.outerHTML = speaker();
      bindSound(fieldEl);
    });
  }

  // ---- drawing ----------------------------------------------------------------------------------------------------
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const resetAt = (me) => fmt.date(me.resets_ts * 1000);
  const themeOf = () => (SCENERY[data.hunt.theme] ? data.hunt.theme : 'jungle');

  function scenery() {
    return SCENERY[themeOf()].map(([e, x, y]) => `<span class="hunt-deco${e === '✨' ? ' firefly' : ''}" style="left:${x}%;top:${y}%" aria-hidden="true">${e}</span>`).join('') +
      (themeOf() === 'rain' ? '<div class="hunt-rain" aria-hidden="true"></div>' : '');
  }
  // One thing on the field. `kind`: banana / golden / bunch (a real one, a button), or rotten (the decoy, not one).
  function itemHtml(p, kind, i) {
    const at = layout().at(p), style = `left:${at.x}px;top:${at.y}px`;
    if (kind === 'rotten') return `<span class="hunt-banana rotten land" style="${style}" aria-hidden="true">🍌</span>`;
    if (kind === 'corn') return `<button type="button" class="hunt-banana corn land" id="hunt-banana" style="${style}" aria-label="Pick the banana">${CORN_SVG}</button>`; // it passes for one
    const label = { golden: 'Pick the golden banana', frozen: 'Crack the frozen banana, then pick it', bouncy: 'Pick the bouncing banana', split: 'Pick the banana',
      green: 'Hold down on the green banana until it ripens', vine: 'Hold down to slide the banana along its vine' }[kind] || 'Pick the banana';
    if (kind === 'frozen' && p.cracked) kind = 'frozen cracked';
    const ring = kind === 'green' ? RING_SVG : ''; // the ring that fills while it's held
    return `<button type="button" class="hunt-banana ${kind} land"${i === undefined ? ' id="hunt-banana"' : ` data-i="${i}"`} style="${style}" aria-label="${label}">${ring}🍌</button>`;
  }
  const RING_SVG = '<svg class="hunt-ring" viewBox="0 0 100 100" aria-hidden="true" focusable="false"><circle class="rail" cx="50" cy="50" r="46"/>' +
    '<circle class="fill" cx="50" cy="50" r="46" pathLength="100"/></svg>';
  // A vine banana's vine, drawn as one: a solid green stem through the server's spots (a smooth curve through them),
  // leaves down both sides of it and a tuft of three at the far end, the part done so far as a pale stripe up the
  // stem, and a ring at the far end to drag to. The leaves are placed from the target's id, so a vine looks the same
  // every time it's drawn. It's under the banana and takes no clicks.
  const LEAF = 'M0 0C4 -24 30 -25 46 0C31 16 10 22 0 0Z'; // one leaf, from its stalk at (0, 0) to its tip, pointing right
  function vineHtml(t) {
    const lay = layout(), pts = t.path.map((q) => lay.at(q)), end = pts[pts.length - 1], z = lay.phone ? 0.7 : 1, L = [0];
    for (let i = 1; i < pts.length; i++) L.push(L[i - 1] + Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y));
    const total = L[L.length - 1], n = (v) => v.toFixed(1);
    let d = `M${n(pts[0].x)} ${n(pts[0].y)}`; // a Catmull-Rom curve through the spots, as cubic Beziers
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i - 1] || pts[i], b = pts[i], c = pts[i + 1], e = pts[i + 2] || c;
      d += `C${n(b.x + (c.x - a.x) / 6)} ${n(b.y + (c.y - a.y) / 6)} ${n(c.x - (e.x - b.x) / 6)} ${n(c.y - (e.y - b.y) / 6)} ${n(c.x)} ${n(c.y)}`;
    }
    let seed = (t.id * 7919 + 13) >>> 0;
    const rnd = () => (seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296;
    // A leaf growing out of the stem `s` px along it, on one side (1 / -1), leaning `lean` degrees off the way the vine runs.
    const leaf = (s, side, size, lean) => {
      const at = vineAt({ pts, L }, s), a = vineAt({ pts, L }, Math.max(0, s - 6)), b = vineAt({ pts, L }, Math.min(total, s + 6));
      const turn = Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI + side * lean;
      return `<path class="leaf" d="${LEAF}" transform="translate(${n(at.x)} ${n(at.y)}) rotate(${n(turn)}) scale(${n(size * z)} ${n(size * z * side)})"/>`;
    };
    let leaves = '', side = rnd() < 0.5 ? 1 : -1;
    for (let s = (44 + rnd() * 16) * z; s < total - 34 * z; s += (40 + rnd() * 26) * z) {
      leaves += leaf(s, side, 0.75 + rnd() * 0.5, 48 + rnd() * 30);
      if (rnd() < 0.3) leaves += leaf(s + 5 * z, -side, 0.6 + rnd() * 0.35, 55 + rnd() * 30); // now and then a pair
      side = -side;
    }
    leaves += [-52, 0, 52].map((lean) => leaf(total, 1, 0.72, lean)).join(''); // the tuft at the tip
    return `<svg class="hunt-vine" width="${lay.w}" height="${lay.h}" aria-hidden="true" focusable="false">${leaves}<path class="stem" d="${d}"/>` +
      `<path class="done" d="${d}" pathLength="100"/><circle class="end" cx="${n(end.x)}" cy="${n(end.y)}" r="${lay.phone ? 22 : 30}"/></svg>`;
  }

  const todaySub = (me, h) => (me.free ? 'all picked: playing on for fun' : me.done ? 'done for today' : me.under_floor ? `past the cap: topping up to ${fmt.credits(h.floor)} credits` : 'credits picked');
  const comboSub = (me, h) => {
    const next = h.combo.steps.find((s) => me.combo < s);
    return me.mult > 1 ? (next ? `x${me.mult} now · x${me.mult + 1} at ${next} in a row` : `x${me.mult}: as high as it goes`) : `x2 at ${h.combo.steps[0]} in a row`;
  };

  function comboHtml() {
    const me = data.me, steps = data.hunt.combo.steps;
    if (!me || me.combo < 2) return '';
    const next = steps.find((s) => me.combo < s), prev = [0, ...steps].filter((s) => s <= me.combo).pop();
    const fill = next ? Math.round(((me.combo - prev) / (next - prev)) * 100) : 100;
    return `<b>x${me.mult}</b><span>${me.combo} in a row</span><i style="width:${fill}%"></i>`;
  }

  function field() {
    const h = data.hunt, me = data.me, lay = layout();
    const size = `width:${lay.w}px;height:${lay.h}px`, cls = `hunt-field hunt-theme-${themeOf()}`;
    if (!me) return `<div class="${cls} hunt-locked" style="${size}"><div class="hunt-msg"><a href="#" data-signin>Sign in</a> to hunt bananas for Onkey. Every one you pick is a credit, up to ${h.daily_max} a day.</div></div>`;
    if (me.done) {
      return `<div class="${cls} hunt-locked" style="${size}">${scenery()}<img class="hunt-onkey full" src="/assets/onkey.png" alt="" aria-hidden="true">` +
        `<div class="hunt-msg"><b>Onkey is full.</b> ${fmt.credits(me.today)} credits picked today. The hunt reopens at ${resetAt(me)} (midnight Pacific). ` +
        `If you drop under ${fmt.credits(h.floor)} credits before then, you can pick back up to ${fmt.credits(h.floor)}.` +
        `<span class="hunt-more"><button type="button" class="btn primary" id="hunt-free">Keep playing</button> Just for fun: no more credits today.</span></div></div>`;
    }
    // The banana isn't in the markup: bind() has Onkey throw it in.
    return `<div class="${cls}" id="hunt-field" style="${size}">${scenery()}<div class="hunt-combo" id="hunt-combo" aria-hidden="true">${comboHtml()}</div>${speaker()}` +
      `<img class="hunt-onkey" id="hunt-onkey" src="/assets/onkey.png" alt="" aria-hidden="true"></div>`;
  }

  function boardCard() {
    const rows = data.board || [];
    const body = rows.map((r, i) => `<tr><td class="rank">${i + 1}</td><td>${plainName(r.name)}</td>` +
      `<td class="num">${r.today ? r.today : '–'}</td><td class="num balance">${fmt.credits(r.season)}</td><td class="num muted">${fmt.credits(r.all_time)}</td></tr>`).join('');
    return `<section class="card"><h2>Top pickers</h2>
      ${how('Who has picked the most bananas for Onkey.', 'This season counts the credits picked since the last season reset; all time counts every season. Today is bananas picked since midnight Pacific.')}
      ${rows.length ? `<div class="table-wrap"><table class="rankings hunt-board"><thead><tr><th class="rank">#</th><th>Picker</th><th class="num">Today</th><th class="num">This season</th><th class="num">All time</th></tr></thead><tbody>${body}</tbody></table></div>`
        : '<p class="muted">Nobody has picked a banana yet. Be the first.</p>'}</section>`;
  }

  function view() {
    if (!data || (state.me?.name || null) !== owner) {
      load().then(() => { if (state.view === 'hunt') draw(); }).catch(() => {
        const el = $('#hunt-loading'); if (el) el.textContent = 'Could not load the hunt. Open this tab again to retry.';
      });
      return '<section class="card" id="hunt-loading">Loading the hunt…</section>';
    }
    const h = data.hunt, me = data.me;
    const tiles = me ? `<section class="kpis">
        ${kpi('Today', `<span id="hunt-today">${me.today}</span> <span class="ov-unit">/ ${h.daily_max}</span>`, `<span id="hunt-today-sub">${todaySub(me, h)}</span>`)}
        ${kpi('Combo', `<span id="hunt-combo-n">${me.combo}</span> <span class="ov-unit">in a row</span>`, `<span id="hunt-combo-sub">${comboSub(me, h)}</span>`)}
        ${kpi('Hunt reopens', resetAt(me), `today's field: ${THEME_NAME[themeOf()]}`)}
      </section>` : '';
    // The right-hand column: what the game is, your tiles for today, and your season (as the casino tables have it).
    const tile = (label, id, value) => `<div class="tile"><div class="tile-label">${label}</div><div class="tile-value" id="${id}">${value}</div></div>`;
    const season = `<section class="card hunt-season"><h2>Your season</h2>${me ? `<div class="kpis small">
        ${tile('Credits', 'hunt-season', fmt.credits(me.season))}${tile('Picks today', 'hunt-picks', fmt.n0(me.picks))}
        ${tile('All time', 'hunt-all-time', fmt.credits(me.all_time))}${tile('Bananas picked', 'hunt-bananas', fmt.n0(me.bananas))}</div>`
      : '<p class="muted"><a href="#" data-signin>Sign in</a> to hunt and see your season.</p>'}</section>`;
    const about = `<section class="card hunt-about"><h2>Banana Hunt</h2>
      ${how(`Onkey throws bananas into the field. Pick one for ${plural(h.per_banana, 'credit')}, or catch it in the air for double, up to ${h.daily_max} credits a day.`,
        `<b>Golden bananas</b> pay ${h.gold.value} but rot ${h.gold.ttl_s} seconds after they land. A <b>bunch</b> is ${h.bunch.size} at once: sweep them all inside ${h.bunch.ttl_s} seconds for ${h.bunch.bonus} more. ` +
        `A brown, <b>rotten banana</b> sometimes lands beside the real one: pick it and you can't pick anything for ${h.freeze_s} seconds. ` +
        `Now and then what Onkey throws is an ear of <b>corn</b>, which is not a banana: pick it and it costs you ${plural(h.corn.cost, 'credit')} and your combo. Leave it and it's gone in ${h.corn.ttl_s} seconds. ` +
        `A <b>frozen banana</b> takes two clicks (the first cracks the ice) and pays ${h.frozen.value}. A <b>bouncing banana</b> pays ${h.bounce.value} but hops to a new spot every ${h.bounce.hop_s} seconds. Any plain-looking banana may turn out to be a <b>split banana</b>: click it and it breaks into ${h.split.size} pieces to sweep up. ` +
        `A <b>green banana</b> isn't ripe, and a click won't pick it: hold the button down on it for ${h.green.hold_s} seconds until the ring fills, and it pays ${h.green.value}. Leave it and it goes brown instead: ${h.green.ttl_s} seconds after it lands it's rotten and gone. ` +
        `A <b>vine banana</b> hangs on a vine: press on it and drag it along the vine to the ring at the far end for ${h.vine.value}. Let go early or stray off the vine and it's back where it started, with nothing lost. ` +
        `Now and then Onkey throws his <b>bongos</b>: each drum has a meter of its own (the ring round it) that fills as you tap the drum and drains when you don't, so tap fast. Fill both for ${h.bongos.value}. ` +
        `A <b>volley</b> is ${h.volley.size} <b>steel bananas</b> thrown one after another: they can't be caught in the air, only picked once they land, where they're ordinary bananas again. Pick every one in time for ${h.volley.bonus} more. Some volleys end with a vine banana, thrown right after the last steel one and landing beside it: drag it too before the time runs out. <b>Greg</b> sometimes walks in to take a banana: pick it first, or click Greg to send him off. ` +
        `Now and then <b>the scientist</b> comes for Onkey himself: his claws come down in ${h.boss.waves.length} waves, each faster than the last, and you click every claw before it reaches Onkey. Stop them all for ${h.boss.prize} credits on top of the day's ${h.daily_max}; let one through and Man Strudel has to set Onkey free, and your combo is gone. Man Strudel himself only wants to say hello. ` +
        `Picks in a row build a <b>combo</b>: every banana pays double from ${h.combo.steps[0]} in a row and triple from ${h.combo.steps[1]}, until you miss, pick a rotten one, lose one to Greg, lose to the scientist, or stop for ${h.combo.idle_s} seconds. ` +
        `One of your picks each day also turns up a <b>hidden item</b>: shop bananas or a daily wheel token. ` +
        `The server places every banana and judges every click, and picks less than ${Math.round(h.min_interval_s * 1000)} ms apart on the ground aren't paid. The day's ${h.daily_max} credits turn over at midnight Pacific, like the daily wheel; ` +
        `the extras only get you there sooner, and only beating the scientist pays past it. Once you've had the day's ${h.daily_max} the hunt closes, though <b>Keep playing</b> lets you carry on for fun, for no credits. There's one exception to the cap, a top-up: if you have fewer than ${fmt.credits(h.floor)} credits you can pick until you have ${fmt.credits(h.floor)}, so nobody is stuck with nothing. ` +
        'Credits from the hunt show in their own column on Standings and stay out of betting profit, like game rewards.')}</section>`;
    const card = `<section class="card hunt-card">
      <div class="hunt-stage"><div class="hunt-fit" style="${fitStyle(layout())}"><div class="hunt-zoom" style="${zoomStyle(layout())}">${field()}</div></div></div></section>`;
    return `<div class="hunt-layout">${card}<aside class="hunt-side">${about}${tiles}${season}</aside></div>${SHOW_BOARD ? boardCard() : ''}`;
  }

  // ---- the throw --------------------------------------------------------------------------------------------------
  // Where a banana thrown to `p` is at progress k, in field pixels: the same arc hunt.py's arc_at() judges a catch on.
  function arcAt(p, k) {
    const [x0, y0] = data.hunt.air.hand;
    const rise = Math.max(110, Math.min(240, Math.hypot(p.x - x0, p.y - y0) * 0.4));
    return { x: x0 + (p.x - x0) * k, y: y0 + (p.y - y0) * k - rise * 4 * k * (1 - k) };
  }

  function clearField(fieldEl) {
    timers.forEach((t) => { if (typeof t === 'number') clearTimeout(t); else { t.oncancel = null; t.onfinish = null; t.cancel(); } });
    timers = [];
    flight = null;
    dropGrip();
    if (drums) { cancelAnimationFrame(drums.raf); drums = null; }
    $$('.hunt-banana, .hunt-fly, .hunt-greg, .hunt-clock, .hunt-msg, .hunt-claw, .hunt-arm, .hunt-boss, .hunt-strudel, .hunt-vine, .hunt-bongos', fieldEl).forEach((el) => el.remove());
    fieldEl.classList.remove('hunt-fight');
  }

  // Everything has landed: the things to pick, then whatever clock belongs to this target.
  function land(fieldEl, t) {
    flight = null;
    const h = data.hunt;
    sfx.land();
    if (t.kind === 'vine') fieldEl.insertAdjacentHTML('beforeend', vineHtml(t)); // under the banana that hangs on it
    if (t.kind === 'bunch') t.items.forEach((it, i) => { if (!it.picked) fieldEl.insertAdjacentHTML('beforeend', itemHtml(it, 'bunch', i)); });
    else if (t.kind === 'bongos') bongosDown(fieldEl, t);
    else fieldEl.insertAdjacentHTML('beforeend', itemHtml(t, t.kind));
    if (t.decoy) fieldEl.insertAdjacentHTML('beforeend', itemHtml(t.decoy, t.decoy.kind || 'rotten'));
    if (t.kind === 'bouncy') { // it hops to its next spot every hop_s, then stays on the last
      hop = { id: t.id, i: 0 };
      t.hops.slice(1).forEach((next, n) => timers.push(setTimeout(() => {
        const el = $('#hunt-banana', fieldEl), at = layout().at(next);
        if (!target || target.id !== t.id || !el) return;
        hop.i = n + 1;
        el.style.left = `${at.x}px`;
        el.style.top = `${at.y}px`;
        el.classList.remove('hop'); void el.offsetWidth; el.classList.add('hop');
        sfx.boing();
      }, (n + 1) * h.bounce.hop_s * 1000)));
    }
    const ttl = t.kind === 'golden' ? h.gold.ttl_s : t.kind === 'bunch' ? h.bunch.ttl_s : 0;
    if (ttl) { // a bar across the top of the field runs down, then the server says what became of it
      fieldEl.insertAdjacentHTML('beforeend', `<div class="hunt-clock ${t.kind}" aria-hidden="true"><i style="animation-duration:${ttl}s"></i></div>`);
      if (t.kind === 'golden') timers.push(setTimeout(() => $('#hunt-banana', fieldEl)?.classList.add('rotting'), ttl * 1000 - 600));
      timers.push(setTimeout(() => nudge(fieldEl, t), ttl * 1000));
    }
    if (t.kind === 'corn') timers.push(setTimeout(() => nudge(fieldEl, t), h.corn.ttl_s * 1000)); // no clock: that would give it away
    if (t.kind === 'green') { // left alone it goes brown from the moment it's down, and then it's gone
      const el = $('#hunt-banana', fieldEl);
      greenDown = performance.now();
      if (el) el.style.setProperty('--ttl', `${h.green.ttl_s}s`);
      const gone = () => { // a hold that's under way is let finish first
        if (grip && grip.kind === 'hold' && grip.id === t.id) timers.push(setTimeout(gone, 120)); else nudge(fieldEl, t);
      };
      timers.push(setTimeout(gone, h.green.ttl_s * 1000));
    }
    if (t.greg) walkGreg(fieldEl, t);
    if (t.strudel) visitStrudel(fieldEl, t);
  }

  // ---- the scientist's boss fight (hunt.py BOSS_*) -----------------------------------------------------------------
  // Now and then no banana comes: the scientist does, for Onkey. His face and his line (in his accent) go up, then Onkey
  // goes to the middle of the field and the claws come in for him from every side on their arms, a wave at a time. Each claw is a button: a click stops it (the
  // server keeps score). Stop a whole wave and the next comes; stop them all and Onkey is safe and the prize is paid.
  // If one gets there, the fight is lost: Man Strudel walks in and sets Onkey free.
  const BOSS_LINES = ['Give me ze monkey. Zis is not a request.', 'Ze claws are coming for him. Stand aside.',
    'You cannot guard him forever. Zis time he comes wiz me.', 'Zat monkey belongs in my laboratory. Step away from him.'];
  let bossShown = ''; // the fight and wave on the field: `${target id}:${wave}`
  function bossWave(fieldEl, t) {
    clearField(fieldEl);
    const b = t.boss, lay = layout(), f = data.hunt.field, to = lay.at({ x: f.w / 2, y: f.h / 2 });
    fieldEl.classList.add('hunt-fight'); // Onkey stands in the middle, and they come for him from every side
    bossShown = `${t.id}:${b.wave}`;
    if (b.wave) sfx.wave(); else sfx.alarm();
    const line = b.wave ? `Wave ${b.wave + 1} of ${b.waves}` : esc(BOSS_LINES[Math.floor(Math.random() * BOSS_LINES.length)]);
    fieldEl.insertAdjacentHTML('beforeend', `<div class="hunt-boss" role="status"><img src="/assets/scientist-face.png" alt="The scientist">` +
      `<div><b>${line}</b><span>Click every claw before it reaches Onkey${b.wave ? '' : `. ${b.waves} waves`}.</span></div></div>`);
    if (!b.wave) window.FiveOnkey?.note('hunt_boss');
    timers.push(setTimeout(() => { // after his line (or the breath between waves), the claws start in
      fieldEl.insertAdjacentHTML('beforeend', `<div class="hunt-clock boss" aria-hidden="true"><i style="animation-duration:${b.secs}s"></i></div>`);
      b.claws.forEach((c) => {
        if (c.hit) return;
        // An arcade claw on an arm: the arm is fixed at the border where it comes in and grows toward Onkey, with the
        // claw (the button) on its end, turned to face him.
        const from = lay.at({ x: c.x, y: c.y }), dx = to.x - from.x, dy = to.y - from.y;
        const dist = Math.hypot(dx, dy), deg = Math.atan2(dy, dx) * 180 / Math.PI, run = { duration: b.secs * 1000, easing: 'linear', fill: 'both' };
        const arm = document.createElement('div');
        arm.className = 'hunt-arm';
        arm.dataset.claw = c.id;
        arm.setAttribute('aria-hidden', 'true');
        arm.style.cssText = `left:${from.x}px;top:${from.y}px;width:${dist}px;transform:rotate(${deg}deg) scaleX(0)`;
        const el = document.createElement('button');
        el.type = 'button';
        el.className = 'hunt-claw';
        el.dataset.claw = c.id;
        el.setAttribute('aria-label', 'Stop the claw');
        el.innerHTML = CLAW_SVG;
        const at = (p) => `translate(${p.x}px, ${p.y}px) translate(-50%, -50%) rotate(${deg - 90}deg)`; // drawn pointing down
        el.style.transform = at(from);
        fieldEl.append(arm, el);
        if (el.animate) {
          timers.push(arm.animate([{ transform: `rotate(${deg}deg) scaleX(0)` }, { transform: `rotate(${deg}deg) scaleX(1)` }], run));
          timers.push(el.animate([{ transform: at(from) }, { transform: at(to) }], run));
        }
      });
      timers.push(setTimeout(() => bossTimeout(fieldEl, t.id, b.wave), b.secs * 1000));
    }, b.wait * 1000));
  }
  // The claw: a hub on the arm's end and three prongs, as on an arcade crane.
  const CLAW_SVG = '<svg viewBox="0 0 64 64" aria-hidden="true" focusable="false"><rect class="hub" x="22" y="1" width="20" height="15" rx="4"/>' +
    '<path class="prong" d="M25 13 C7 19 4 39 15 59 L22 54 C15 41 18 29 31 22 Z"/><path class="prong" d="M39 13 C57 19 60 39 49 59 L42 54 C49 41 46 29 33 22 Z"/>' +
    '<path class="prong mid" d="M28 14 h8 v32 l-4 10 l-4 -10 Z"/><circle class="bolt" cx="32" cy="9" r="3"/></svg>';
  // The server's answer to a click during the fight: the stopped claws go, or the next wave comes.
  function bossSync(fieldEl, t) {
    if (`${t.id}:${t.boss.wave}` !== bossShown) { sfx.waveClear(); pop(fieldEl, fieldEl.clientWidth / 2, fieldEl.clientHeight / 2, 'Wave cleared!', 'air'); bossWave(fieldEl, t); return; }
    t.boss.claws.forEach((c) => { if (c.hit) $$(`[data-claw="${c.id}"]`, fieldEl).forEach((el) => el.remove()); });
  }
  function hitClaw(el, fieldEl) {
    if (el.classList.contains('hit')) return;
    el.classList.add('hit'); // it stops at once; the server confirms
    sfx.claw();
    $(`.hunt-arm[data-claw="${el.dataset.claw}"]`, fieldEl)?.classList.add('hit');
    post('/api/hunt/click', { x: 0, y: 0, claw: Number(el.dataset.claw) })
      .then((r) => { if (document.body.contains(fieldEl)) apply(r, fieldEl); })
      .catch(() => $$(`[data-claw="${el.dataset.claw}"]`, fieldEl).forEach((x) => x.classList.remove('hit')));
  }
  // A wave's time is up on the page: ask the server whether a claw got there (it allows a little slack).
  async function bossTimeout(fieldEl, id, wave, tries = 0) {
    const mine = () => target && target.kind === 'boss' && target.id === id && target.boss.wave === wave && document.body.contains(fieldEl);
    if (!mine()) return;
    try {
      const r = await post('/api/hunt/next');
      if (!mine()) return;
      if (r.target && r.target.kind === 'boss' && r.target.id === id && r.target.boss.wave === wave && tries < 6) { timers.push(setTimeout(() => bossTimeout(fieldEl, id, wave, tries + 1), 350)); return; }
      apply(r, fieldEl);
    } catch (err) { /* the next click sorts it out */ }
  }
  // Lost: a claw has Onkey. Man Strudel (friendly, no accent) walks in and sets him free, then the hunt carries on.
  function rescue(fieldEl) {
    clearField(fieldEl);
    const lay = layout(), f = data.hunt.field, to = lay.at({ x: f.w / 2 - 170, y: f.h / 2 + 60 }), from = lay.at({ x: -160, y: f.h / 2 + 60 });
    fieldEl.classList.add('hunt-fight'); // Onkey is still in the middle, where the claw got him
    fieldEl.insertAdjacentHTML('beforeend', '<div class="hunt-boss lost" role="status"><img src="/assets/scientist-face.png" alt="The scientist"><div><b>Ze monkey is mine!</b><span>A claw got to Onkey.</span></div></div>');
    const el = document.createElement('div');
    el.className = 'hunt-strudel asking';
    el.innerHTML = '<span class="hunt-strudel-say">Strudel will let Onkey go.</span><img src="/assets/man-strudel-small.png" alt="Man Strudel"><small>Man Strudel</small>';
    fieldEl.appendChild(el);
    const at = (p) => `translate(${p.x}px, ${p.y}px) translate(-50%, -72%)`;
    if (el.animate) timers.push(el.animate([{ transform: at(from), offset: 0 }, { transform: at(to), offset: 0.45 }, { transform: at(to), offset: 0.8 }, { transform: at(from), offset: 1 }], { duration: 3200, easing: 'linear', fill: 'both' }));
    window.FiveOnkey?.note('hunt_boss_lost');
    sfx.bossLost();
    timers.push(setTimeout(() => restart(fieldEl), 3200));
  }
  // The server puts a banana down (or starts the timers of the one that's there again), and Onkey throws it in.
  function restart(fieldEl) {
    return post('/api/hunt/start').then((r) => {
      if (!document.body.contains(fieldEl)) return;
      data.me = r.me; target = r.me.target; throwTo(fieldEl, target);
    }).catch(() => {});
  }

  // Man Strudel, the scientist's henchman (enormous, his head in the brainbot's dome, and friendly): he walks up, asks
  // to pet Onkey, and leaves without taking anything. He doesn't have the scientist's accent.
  function visitStrudel(fieldEl, t) {
    const lay = layout(), from = lay.at(t.strudel), to = lay.at(t);
    const side = to.x < from.x ? 1 : -1, stop = { x: to.x + side * 96, y: to.y };
    const el = document.createElement('div');
    el.className = 'hunt-strudel';
    el.setAttribute('aria-hidden', 'true');
    el.innerHTML = '<span class="hunt-strudel-say">May I pet Onkey?</span><img src="/assets/man-strudel-small.png" alt=""><small>Man Strudel</small>';
    fieldEl.appendChild(el);
    if (!el.animate) { el.remove(); return; }
    sfx.strudel();
    const at = (p) => `translate(${p.x}px, ${p.y}px) translate(-50%, -72%)`;
    const visit = el.animate([{ transform: at(from), offset: 0 }, { transform: at(stop), offset: 0.3 }, { transform: at(stop), offset: 0.72 }, { transform: at(from), offset: 1 }],
      { duration: 4200, easing: 'linear', fill: 'both' });
    timers.push(setTimeout(() => el.classList.add('asking'), 1260));
    timers.push(setTimeout(() => el.classList.remove('asking'), 3000));
    visit.onfinish = () => el.remove();
    timers.push(visit);
  }

  // Greg comes in from the edge and walks to the banana; if he gets there, he has it.
  function walkGreg(fieldEl, t) {
    const lay = layout(), from = lay.at(t.greg), to = lay.at(t);
    const greg = document.createElement('img');
    greg.className = 'hunt-greg';
    greg.src = '/assets/greg-logo.png';
    greg.alt = '';
    greg.setAttribute('aria-hidden', 'true');
    fieldEl.appendChild(greg);
    sfx.greg();
    if (!greg.animate) return;
    const flip = to.x < from.x ? ' scaleX(-1)' : '';
    const walk = greg.animate([{ transform: `translate(${from.x}px, ${from.y}px) translate(-50%, -60%)${flip}` },
      { transform: `translate(${to.x}px, ${to.y}px) translate(-50%, -60%)${flip}` }], { duration: data.hunt.greg_s * 1000, easing: 'linear', fill: 'both' });
    walk.onfinish = () => nudge(fieldEl, t);
    timers.push(walk);
  }
  function shooGreg(fieldEl) {
    const greg = $('.hunt-greg', fieldEl);
    if (!greg) return;
    const box = greg.getBoundingClientRect(), f = fieldEl.getBoundingClientRect(), k = layout().k, wide = fieldEl.clientWidth;
    const x = (box.left + box.width / 2 - f.left) / k, y = (box.top + box.height / 2 - f.top) / k, away = x < wide / 2 ? -140 : wide + 140;
    timers = timers.filter((t) => { if (typeof t === 'number') return true; t.onfinish = null; t.cancel(); return false; });
    greg.classList.add('shooed');
    if (greg.animate) greg.animate([{ transform: `translate(${x}px, ${y}px) translate(-50%, -50%)` }, { transform: `translate(${away}px, ${y - 30}px) translate(-50%, -50%) rotate(${x < wide / 2 ? -40 : 40}deg)` }],
      { duration: 450, easing: 'ease-in', fill: 'both' }).onfinish = () => greg.remove();
    else greg.remove();
  }

  // Onkey winds up in his corner and each banana flies along an arc to where the server put it, spinning, and lands
  // with a squash. A plain or golden one can be caught on the way.
  function throwTo(fieldEl, t) {
    if (t && t.kind === 'boss') { bossWave(fieldEl, t); return; } // no banana this time: the scientist
    clearField(fieldEl);
    if (!t) return;
    if (!('animate' in Element.prototype)) { land(fieldEl, t); return; }
    if (t.kind === 'volley') { throwVolley(fieldEl, t); return; } // one after another, not all at once
    const onkey = $('#hunt-onkey', fieldEl);
    if (onkey) { onkey.classList.remove('throw'); void onkey.offsetWidth; onkey.classList.add('throw'); }
    sfx.throw(THROW_DELAY / 1000);
    const lay = layout();
    const spots = t.kind === 'bunch' ? t.items.filter((it) => !it.picked).map((it) => ({ p: it, cls: 'bunch' })) : [{ p: t, cls: t.kind }];
    if (t.decoy) spots.push({ p: t.decoy, cls: t.decoy.kind || 'rotten' });
    let anim = null;
    spots.forEach(({ p, cls }) => {
      const a = fly(fieldEl, p, cls, lay, 0).anim;
      timers.push(a);
      if (!anim) anim = a;
    });
    flight = { t, anim, catchable: !['bunch', 'green', 'vine', 'bongos'].includes(t.kind) }; // those are held, dragged or played, once down
    let landed = false;
    const finish = () => { // once, whether the animation finishes or never reports back (a hidden tab)
      if (landed) return;
      landed = true;
      $$('.hunt-fly', fieldEl).forEach((el) => el.remove());
      // The target may have been answered about while it flew (a frozen one cracked, a click the server didn't count),
      // which replaces the object but not what's on the field: it lands as the server last described it.
      if (target && target.id === t.id) land(fieldEl, target); else flight = null;
    };
    timers.push(setTimeout(finish, THROW_DELAY + THROW_MS + 600));
    anim.onfinish = finish;
  }
  // The corn: a bare ear with no husk, drawn as an ear of corn is (a fat cob, tilted, tapering to the tip, rows of
  // rounded kernels with dark gaps between them, lit from the left) at a banana's size and in a banana's yellows.
  const CORN_SVG = (() => {
    const half = (y) => 13.5 * Math.sqrt(Math.max(0, 1 - ((y - 34) / 28) ** 2)) * (0.8 + 0.2 * (y / 64)); // narrower toward the tip
    const mix = (k) => `rgb(${Math.round(255 - 22 * k)}, ${Math.round(222 - 74 * k)}, ${Math.round(70 - 46 * k)})`; // yellow to orange
    let outline = '', back = '', kernels = '';
    for (let y = 6; y <= 62; y += 2) outline += `${outline ? 'L' : 'M'}${(32 - half(y) - 1.6).toFixed(1)} ${y}`;
    for (let y = 62; y >= 6; y -= 2) back += `L${(32 + half(y) + 1.6).toFixed(1)} ${y}`;
    const COLS = 6, ROW = 4.3;
    for (let y = 8; y < 60; y += ROW) {
      for (let c = 0; c < COLS; c++) { // columns evenly spaced round the cob, so the ones at its edges look narrower
        const a0 = -Math.PI / 2 + Math.PI * c / COLS, a1 = a0 + Math.PI / COLS, mid = (a0 + a1) / 2;
        const yy = y + 1.6 * Math.cos(mid), w = half(yy + ROW / 2); // each row bows toward the viewer
        const x0 = 32 + w * Math.sin(a0) + 0.35, x1 = 32 + w * Math.sin(a1) - 0.35;
        if (x1 - x0 < 0.9) continue;
        kernels += `<rect x="${x0.toFixed(2)}" y="${yy.toFixed(2)}" width="${(x1 - x0).toFixed(2)}" height="${(ROW - 0.8).toFixed(2)}" rx="1.3" fill="${mix((c + (y % 3) * 0.12) / (COLS - 0.6))}"/>`;
      }
    }
    return '<svg class="hunt-cob" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g transform="rotate(32 32 33)">' +
      `<path d="${outline}${back}Z" fill="#6b4410" stroke="#4a2e08" stroke-width="1.2" stroke-linejoin="round"/>${kernels}</g></svg>`;
  })();
  // A volley's steel banana, as it flies: drawn rather than an emoji, as a cartoon of a banana built from steel plate:
  // a fat grey body under a heavy black outline, glossy along its inner curve and dark along its belly, a few big
  // panels (seams across it, and one down the lower half), a domed rivet at every panel corner, a flat-topped stem
  // and a round nub at the tip. Once it lands it's an ordinary banana (.hunt-banana.volley).
  const STEEL_SVG = (() => {
    const OUT = [[52, 12], [60, 37], [40, 60], [11, 50.5]], IN = [[45.5, 10.5], [39, 24], [28, 34], [9, 40.5]]; // its belly and its inner edge, stem to tip
    const BODY = 'M45.5 10.5L45 4.5L51.5 3.5L52 12C60 37 40 60 11 50.5C7.5 49.5 6.5 43 9 40.5C28 34 39 24 45.5 10.5Z';
    const bez = (c, t) => [0, 1].map((i) => (1 - t) ** 3 * c[0][i] + 3 * (1 - t) ** 2 * t * c[1][i] + 3 * (1 - t) * t * t * c[2][i] + t ** 3 * c[3][i]);
    const at = (t, k) => { const a = bez(OUT, t), b = bez(IN, t); return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k]; }; // t along it, k from the belly across
    const xy = (q) => `${q[0].toFixed(1)} ${q[1].toFixed(1)}`;
    const run = (k, from, to) => { const out = []; for (let i = 0; i <= 12; i++) out.push(at(from + (to - from) * i / 12, k)); return out; }; // along it, at k across
    const d = (pts) => pts.map((q, i) => `${i ? 'L' : 'M'}${xy(q)}`).join('');
    // The shading: bands down its length, blurred into each other (dark belly, a bright streak of gloss, a darker rim).
    const band = (k0, k1, fill) => `<path fill="${fill}" d="${d(run(k0, 0, 1).concat(run(k1, 0, 1).reverse()))}Z"/>`;
    const shade = band(0, 0.14, '#5f5f5f') + band(0.14, 0.32, '#7d7d7d') + band(0.5, 0.62, '#bcbcbc') + band(0.62, 0.8, '#f1f1f1') + band(0.8, 0.9, '#b4b4b4') + band(0.9, 1, '#808080');
    const SEAMS = [0.2, 0.5, 0.8], LONG = 0.4;
    let seams = d(run(LONG, SEAMS[0], 0.98)), rivets = '<circle cx="48.4" cy="7.6" r="1.3"/>';
    SEAMS.forEach((t) => {
      seams += `M${xy(at(t, 0))}L${xy(at(t, 1))}`;
      [0.16, 0.84].forEach((k) => [t - 0.055, t + 0.055].forEach((tt) => { const q = at(tt, k); rivets += `<circle cx="${q[0].toFixed(1)}" cy="${q[1].toFixed(1)}" r="1.65"/>`; }));
    });
    return '<svg class="hunt-steel" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><defs>' +
      `<clipPath id="hunt-steel-clip"><path d="${BODY}"/></clipPath><filter id="hunt-steel-soft" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="1.1"/></filter>` +
      '<radialGradient id="hunt-steel-rivet" cx="0.36" cy="0.3" r="0.8"><stop offset="0" stop-color="#ffffff"/><stop offset="0.45" stop-color="#cdcdcd"/><stop offset="1" stop-color="#767676"/></radialGradient></defs>' +
      `<path fill="#a1a1a1" d="${BODY}"/><g clip-path="url(#hunt-steel-clip)"><g filter="url(#hunt-steel-soft)">${shade}</g></g>` +
      `<path class="seam" d="${seams}"/><path class="edge" d="${BODY}"/><path class="cap" d="M44.2 5.4L44.6 2.9L51.8 1.9L52.4 4.5Z"/>` +
      `<circle class="nub" fill="url(#hunt-steel-rivet)" cx="8" cy="45.5" r="3.2"/><g class="rivets" fill="url(#hunt-steel-rivet)">${rivets}</g></svg>`;
  })();
  // One thing in the air, from Onkey's hand along its arc to `p`, starting `wait` ms after the wind-up.
  function fly(fieldEl, p, cls, lay, wait) {
    const el = document.createElement('span');
    el.className = `hunt-fly ${cls}`;
    if (cls === 'corn') el.innerHTML = CORN_SVG; else if (cls === 'volley') el.innerHTML = STEEL_SVG; else if (cls === 'bongos') el.innerHTML = bongosSvg(false); else el.textContent = '🍌';
    el.setAttribute('aria-hidden', 'true');
    fieldEl.appendChild(el);
    const frames = [], N = 30;
    for (let i = 0; i <= N; i++) {
      const k = i / N, at = lay.at(arcAt(p, k));
      frames.push({ offset: k, transform: `translate(${at.x.toFixed(1)}px, ${at.y.toFixed(1)}px) translate(-50%, -50%) rotate(${Math.round(k * 540)}deg) scale(${(1 + 0.35 * Math.sin(Math.PI * k)).toFixed(3)})` });
    }
    el.style.transform = frames[0].transform; // in Onkey's hand during the wind-up, not at the field's corner
    return { el, anim: el.animate(frames, { duration: THROW_MS, delay: THROW_DELAY + wait, easing: 'linear', fill: 'both' }) };
  }
  // A volley: Onkey throws steel bananas one after another (gap_s apart) along a line or an arc. Steel can't be caught:
  // each is picked once it's down (where it's a plain banana again), and when the last has landed a clock runs on what's left. Each one is made as it's
  // thrown, so the ones still to come don't sit in his hand. Some volleys end with a vine banana (`t.vine`): thrown
  // straight after the last steel one, it lands by it, on its vine, and is dragged while the rest are still there.
  function throwVolley(fieldEl, t) {
    const lay = layout(), h = data.hunt, gap = h.volley.gap_s * 1000, onkey = $('#hunt-onkey', fieldEl);
    flight = null;
    t.items.forEach((it, i) => {
      if (it.picked) return;
      timers.push(setTimeout(() => {
        if (onkey) { onkey.classList.remove('throw'); void onkey.offsetWidth; onkey.classList.add('throw'); }
        sfx.throw(THROW_DELAY / 1000);
        const f = fly(fieldEl, it, 'volley', lay, 0);
        f.anim.onfinish = () => {
          f.el.remove();
          sfx.clang(0.45); // steel on the ground
          if (target && target.id === t.id && !target.items[i].picked) fieldEl.insertAdjacentHTML('beforeend', itemHtml(it, 'volley', i));
        };
        timers.push(f.anim);
      }, i * gap));
    });
    const tail = vineOf(t), throws = t.items.length + (tail ? 1 : 0), ttl = h.volley.ttl_s + (tail ? h.volley.vine_ttl_s : 0);
    if (tail) {
      timers.push(setTimeout(() => {
        if (onkey) { onkey.classList.remove('throw'); void onkey.offsetWidth; onkey.classList.add('throw'); }
        sfx.throw(THROW_DELAY / 1000);
        const f = fly(fieldEl, tail, 'vine', lay, 0);
        f.anim.onfinish = () => {
          f.el.remove();
          const v = target && target.id === t.id ? vineOf(target) : null;
          if (!v) return;
          sfx.land();
          fieldEl.insertAdjacentHTML('beforeend', vineHtml(v) + itemHtml(v, 'vine'));
        };
        timers.push(f.anim);
      }, t.items.length * gap));
    }
    const down = THROW_DELAY + (throws - 1) * gap + THROW_MS;
    timers.push(setTimeout(() => fieldEl.insertAdjacentHTML('beforeend', `<div class="hunt-clock bunch" aria-hidden="true"><i style="animation-duration:${ttl}s"></i></div>`), down));
    timers.push(setTimeout(() => nudge(fieldEl, t), down + ttl * 1000));
  }
  // ---- Onkey's bongos (hunt.py BONGO_*) ----------------------------------------------------------------------------
  // Now and then what Onkey throws is his pair of bongos. Each drum has a meter of its own (the ring round it): a tap
  // on the drum fills it a little, and it drains all the while, so only quick tapping gets it to the top, where it
  // stays. Both full, and the taps go to the server, which plays them back the same way (so the sums here are done on
  // the same whole milliseconds that are sent). Enter or Space on a drum taps it too.
  const BONGO = [{ cx: 48, cy: 60, r: 36, name: 'small' }, { cx: 147, cy: 56, r: 46, name: 'big' }]; // in the drawing's own 200 x 112
  let drums = null; // the bongos on the field: {id, m (each meter as of its last tap), last (when that was), full, taps, t0, raf, done}
  // The pair from above: two skin heads in chrome rims with their tuning lugs, joined by a block of wood. With
  // `meters`, each has its ring.
  function bongosSvg(meters) {
    const drum = (b, i) => {
      let lugs = '';
      [45, 135, 225, 315].forEach((deg) => { const a = deg * Math.PI / 180; lugs += `<circle class="lug" cx="${(b.cx + b.r * Math.cos(a)).toFixed(1)}" cy="${(b.cy + b.r * Math.sin(a)).toFixed(1)}" r="3.6"/>`; });
      const ring = meters ? `<circle class="rail" cx="${b.cx}" cy="${b.cy}" r="${b.r + 7}"/><circle class="meter" cx="${b.cx}" cy="${b.cy}" r="${b.r + 7}" pathLength="100" transform="rotate(-90 ${b.cx} ${b.cy})"/>` : '';
      return `<g class="drum" data-drum="${i}">${ring}<circle class="rim" cx="${b.cx}" cy="${b.cy}" r="${b.r}"/><circle class="rim-shine" cx="${b.cx}" cy="${b.cy}" r="${b.r - 2.2}"/>` +
        `<circle class="skin" fill="url(#hunt-bongo-skin)" cx="${b.cx}" cy="${b.cy}" r="${b.r - 5}"/>${lugs}</g>`;
    };
    return '<svg class="hunt-bongos-art" viewBox="0 0 200 112" aria-hidden="true" focusable="false"><defs><radialGradient id="hunt-bongo-skin" cx="0.42" cy="0.38" r="0.78">' +
      '<stop offset="0" stop-color="#f5eddc"/><stop offset="0.65" stop-color="#e5d9bf"/><stop offset="1" stop-color="#c8b796"/></radialGradient></defs>' +
      `<rect class="block" x="78" y="45" width="28" height="24" rx="3"/>${BONGO.map(drum).join('')}</svg>`;
  }
  const bongoSize = () => (layout().phone ? 0.72 : 1); // how big they're drawn
  // The bongos have landed: draw them and start their meters.
  function bongosDown(fieldEl, t) {
    const at = layout().at(t), z = bongoSize();
    const key = (b, i) => `<button type="button" class="hunt-bongo" data-drum="${i}" aria-label="Play the ${b.name} bongo: tap it fast to fill its meter" ` +
      `style="left:${(b.cx - b.r) / 2}%;top:${(b.cy - b.r) / 1.12}%;width:${b.r}%;height:${b.r * 2 / 1.12}%"></button>`;
    fieldEl.insertAdjacentHTML('beforeend', `<div class="hunt-bongos" id="hunt-bongos" style="left:${at.x}px;top:${at.y}px;width:${200 * z}px;height:${112 * z}px">${bongosSvg(true)}${BONGO.map(key).join('')}</div>`);
    const s = drums = { id: t.id, m: [0, 0], last: [null, null], full: [false, false], taps: [], t0: null, raf: 0, done: false };
    const meters = $$('#hunt-bongos .meter', fieldEl);
    const paint = () => { // the rings follow the meters as they drain
      if (drums !== s) return;
      const ms = s.t0 === null ? 0 : performance.now() - s.t0;
      meters.forEach((el, d) => { el.style.strokeDashoffset = 100 - 100 * Math.min(1, bongoMeter(s, d, ms)); });
      s.raf = requestAnimationFrame(paint);
    };
    paint();
  }
  // How full a drum's meter is `ms` after the first tap.
  function bongoMeter(s, d, ms) {
    if (s.full[d]) return 1;
    return s.last[d] === null ? 0 : Math.max(0, s.m[d] - data.hunt.bongos.drain * (ms - s.last[d]) / 1000);
  }
  // Which drum a click at `shown` (the field's own pixels) is on: 0, 1, or null.
  function drumAt(shown) {
    const lay = layout(), at = lay.at(target), z = bongoSize();
    const i = BONGO.findIndex((b) => Math.hypot(shown.x - (at.x + (b.cx - 100) * z), shown.y - (at.y + (b.cy - 56) * z)) <= (b.r + 8) * z + (lay.phone ? 6 : 0));
    return i < 0 ? null : i;
  }
  function tapDrum(fieldEl, d) {
    const s = drums, h = data.hunt.bongos;
    if (!s || s.done || s.full[d]) return;
    const now = performance.now();
    let ms = s.t0 === null ? 0 : Math.round(now - s.t0);
    if (s.taps.length && ms - s.taps[s.taps.length - 1][1] < h.min_gap_ms + 5) return; // faster than a hand: not counted
    // Nothing banked (no drum full, both meters run down), or far too many taps to send: the count starts again here.
    if (s.t0 === null || s.taps.length > 360 || (!s.full[0] && !s.full[1] && bongoMeter(s, 0, ms) === 0 && bongoMeter(s, 1, ms) === 0)) {
      Object.assign(s, { m: [0, 0], last: [null, null], full: [false, false], taps: [], t0: now });
      $$('#hunt-bongos .drum', fieldEl).forEach((g) => g.classList.remove('full'));
      ms = 0;
    }
    s.m[d] = bongoMeter(s, d, ms) + h.gain;
    s.last[d] = ms;
    s.taps.push([d, ms]);
    sfx.bongo(d);
    const g = $(`#hunt-bongos .drum[data-drum="${d}"]`, fieldEl);
    if (g) { g.classList.remove('hit'); void g.getBoundingClientRect(); g.classList.add('hit'); }
    if (s.m[d] >= 1 - 1e-9) { s.full[d] = true; if (g) g.classList.add('full'); sfx.drumFull(); }
    if (!s.full[0] || !s.full[1]) return;
    s.done = true; // both full: off it goes (after any click that's still on its way to the server)
    const t = target, shown = layout().at(t);
    (function go() {
      if (drums !== s) return;
      if (busy) { setTimeout(go, 40); return; }
      send({ x: t.x, y: t.y, taps: s.taps }, fieldEl, shown);
    })();
  }
  // The server didn't count it: the meters start again.
  function bongosReset(fieldEl) {
    if (!drums) return;
    Object.assign(drums, { m: [0, 0], last: [null, null], full: [false, false], taps: [], t0: null, done: false });
    $$('#hunt-bongos .drum', fieldEl).forEach((g) => g.classList.remove('full'));
  }

  // ---- hold and drag (hunt.py GREEN_*, VINE_*) ---------------------------------------------------------------------
  // Two bananas aren't picked with a click. A green one ripens while the button is held down on it: its ring fills in
  // hold_s and then it's picked by itself. A vine one is dragged along its vine: the banana follows the pointer
  // forward along the path (never back), and reaching the far end picks it. Letting go early, or straying further
  // than `reach` from the banana, puts it back as it was, with nothing sent and nothing lost. The server checks the
  // hold's length (`held`) and the drag (`trail`: where the pointer was, and when, as the banana passed each of the
  // path's spots) against its own clock. With the keyboard, holding Enter or Space does either: the green one
  // ripens, the vine one slides along at VINE_KEY_SPEED.
  const VINE_STEP = 8; // a pointer move is followed in steps no longer than this (px as drawn), however fast it was
  const VINE_AHEAD = 24; // how far along the vine one step can take the banana
  const VINE_KEY_SPEED = 420; // px a second along the vine, with the keyboard
  function grab(fieldEl, g) {
    grip = { timer: 0, raf: 0, done: false, id: target.id, fieldEl, ...g };
    if (g.pointer !== null) { try { fieldEl.setPointerCapture(g.pointer); } catch (err) { /* not a pointer that can be held: its events still reach the field */ } }
    g.el.classList.remove('land');
    return grip;
  }
  function dropGrip() {
    const g = grip;
    if (!g) return;
    grip = null;
    clearTimeout(g.timer);
    cancelAnimationFrame(g.raf);
    g.el.classList.remove('holding', 'dragging');
    if (g.kind === 'hold') { // back to going brown, from as brown as it would be by now
      sfx.hum(0);
      g.el.style.filter = '';
      g.el.style.setProperty('--aged', `${(-(performance.now() - greenDown) / 1000).toFixed(2)}s`);
    }
    g.fieldEl.classList.remove('hunt-dragging');
    if (g.pointer !== null) { try { g.fieldEl.releasePointerCapture(g.pointer); } catch (err) { /* it had let go already */ } }
  }
  // When it's done: the pick goes to the server (after any click that's still on its way there).
  function gripDone(body, shown) {
    const g = grip, fieldEl = g.fieldEl;
    g.done = true;
    (function go() {
      if (grip !== g) return;
      if (busy) { g.timer = setTimeout(go, 40); return; }
      dropGrip();
      g.el.classList.add('picked');
      send(body(), fieldEl, shown);
    })();
  }
  // It came up early (or strayed): everything back as it was.
  function letGo() {
    const g = grip;
    if (!g || g.done) return;
    dropGrip();
    if (g.kind === 'drag') vineHome(g.fieldEl);
    sfx.slip();
    const at = g.kind === 'drag' ? vineAt(g, g.s) : g.shown;
    pop(g.fieldEl, at.x, at.y, g.kind === 'hold' ? 'Hold it!' : g.s < 4 ? 'Drag it!' : 'Slipped!', 'air');
  }

  function startHold(fieldEl, el, shown, pointer) {
    const t = target, lay = layout(), need = data.hunt.green.hold_s * 1000;
    const g = grab(fieldEl, { kind: 'hold', el, shown, home: lay.at(t), pointer, t0: performance.now(), reach: (lay.phone ? TAP_R : data.hunt.field.r) + 14 });
    el.style.setProperty('--hold', `${need}ms`);
    el.style.filter = getComputedStyle(el).filter; // it ripens from however brown it has gone
    el.classList.add('holding');
    sfx.hum(need / 1000);
    g.timer = setTimeout(() => {
      if (grip !== g) return;
      const at = lay.phone ? t : lay.toField(shown.x, shown.y);
      gripDone(() => ({ x: Math.round(at.x), y: Math.round(at.y), held: Number(((performance.now() - g.t0) / 1000).toFixed(3)) }), lay.at(t));
    }, need + 30);
  }

  // The vine banana on the field, if there is one to drag: a vine target itself, or the one thrown on the end of a
  // volley (its `vine`), as {id, x, y, path}.
  function vineOf(t) {
    if (t && t.kind === 'vine') return t;
    return t && t.kind === 'volley' && t.vine && !t.vine.done ? { id: t.id, x: t.vine.path[0].x, y: t.vine.path[0].y, path: t.vine.path } : null;
  }
  function startDrag(fieldEl, el, shown, pointer) {
    const t = vineOf(target), lay = layout(), pts = t.path.map((q) => lay.at(q)), L = [0];
    for (let i = 1; i < pts.length; i++) L.push(L[i - 1] + Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y));
    // What's reported for each spot of the path: where the pointer was (on a phone the spot itself, as a tap is) and when.
    const sample = (i, q, ms) => { const at = lay.phone ? t.path[i] : lay.toField(q.x, q.y); return [Math.round(at.x), Math.round(at.y), Math.round(ms)]; };
    const g = grab(fieldEl, { kind: 'drag', el, pointer, pts, L, sample, path: t.path, s: 0, next: 1, last: shown, t0: performance.now(),
      reach: lay.phone ? TAP_R + 10 : data.hunt.vine.r - 30, trail: [sample(0, shown, 0)] });
    el.classList.add('dragging');
    fieldEl.classList.add('hunt-dragging');
    if (pointer !== null) return;
    const tick = (now) => { // the keyboard: it slides by itself while the key is down
      if (grip !== g || g.done) return;
      dragTo(vineAt(g, Math.min(L[L.length - 1], Math.max(0, now - g.t0) / 1000 * VINE_KEY_SPEED)));
      if (grip === g && !g.done) g.raf = requestAnimationFrame(tick);
    };
    g.raf = requestAnimationFrame(tick);
  }
  // Where the vine is, `s` px along it.
  function vineAt(g, s) {
    let i = 1;
    while (i < g.L.length - 1 && g.L[i] < s) i += 1;
    const a = g.pts[i - 1], b = g.pts[i], k = Math.max(0, Math.min(1, (s - g.L[i - 1]) / ((g.L[i] - g.L[i - 1]) || 1)));
    return { x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k };
  }
  // The point of the vine nearest `q`, looking only from where the banana is to VINE_AHEAD further on: {s, d}.
  function vineAhead(g, q) {
    const lo = g.s, hi = Math.min(g.L[g.L.length - 1], g.s + VINE_AHEAD);
    let best = { s: lo, d: Infinity };
    for (let i = 1; i < g.pts.length; i++) {
      if (g.L[i] < lo || g.L[i - 1] > hi) continue;
      const a = g.pts[i - 1], b = g.pts[i], len = (g.L[i] - g.L[i - 1]) || 1;
      const k = ((q.x - a.x) * (b.x - a.x) + (q.y - a.y) * (b.y - a.y)) / (len * len);
      const s = Math.max(lo, Math.min(hi, g.L[i - 1] + Math.max(0, Math.min(1, k)) * len)), at = vineAt(g, s);
      const d = Math.hypot(q.x - at.x, q.y - at.y);
      if (d < best.d) best = { s, d };
    }
    return best;
  }
  // The pointer is at `q` now (the field's own pixels): the banana follows it along the vine.
  function dragTo(q) {
    const g = grip, from = g.last, n = Math.max(1, Math.ceil(Math.hypot(q.x - from.x, q.y - from.y) / VINE_STEP));
    for (let j = 1; j <= n; j++) {
      const at = { x: from.x + (q.x - from.x) * j / n, y: from.y + (q.y - from.y) * j / n }, hit = vineAhead(g, at);
      if (hit.d > g.reach) { letGo(); return; }
      g.s = hit.s;
      while (g.next < g.pts.length && g.s >= g.L[g.next] - 0.5) { g.trail.push(g.sample(g.next, at, performance.now() - g.t0)); g.next += 1; sfx.zip(g.next / g.pts.length); }
      if (g.next === g.pts.length) break;
    }
    g.last = q;
    vinePaint(g);
    if (g.next < g.pts.length) return;
    const end = g.path[g.path.length - 1];
    gripDone(() => ({ x: end.x, y: end.y, trail: g.trail }), g.pts[g.pts.length - 1]);
  }
  function vinePaint(g) {
    const at = vineAt(g, g.s), done = $('.hunt-vine .done', g.fieldEl);
    g.el.style.left = `${at.x}px`;
    g.el.style.top = `${at.y}px`;
    if (done) done.style.strokeDashoffset = 100 - 100 * g.s / g.L[g.L.length - 1];
  }
  // The vine banana back at the start of its vine.
  function vineHome(fieldEl) {
    const el = $('#hunt-banana', fieldEl), done = $('.hunt-vine .done', fieldEl);
    const v = vineOf(target);
    if (!el || !v) return;
    const at = layout().at(v);
    el.style.left = `${at.x}px`;
    el.style.top = `${at.y}px`;
    if (done) done.style.strokeDashoffset = 100;
  }

  // How far along its arc the banana in the air is (0-1), or null when nothing can be caught.
  function airK() {
    if (!flight || !flight.catchable) return null;
    const ms = Number(flight.anim.currentTime) - THROW_DELAY;
    return ms > 0 ? Math.min(1, ms / THROW_MS) : null;
  }

  function pop(fieldEl, x, y, text, cls = '') {
    const el = document.createElement('span');
    el.className = `hunt-pop ${cls}`;
    el.textContent = text;
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
    fieldEl.appendChild(el);
    setTimeout(() => el.remove(), cls ? 1100 : 750);
  }
  const popAt = (fieldEl, p, text, cls) => { const at = layout().at(p); pop(fieldEl, at.x, at.y, text, cls); };
  // The ice on a frozen banana shatters: a flash where it was, and shards that fly out from it, turning, and fade.
  function shatter(fieldEl, x, y) {
    const add = (cls, style) => {
      const el = document.createElement('span');
      el.className = cls;
      el.setAttribute('aria-hidden', 'true');
      el.style.cssText = `left:${x}px;top:${y}px;${style}`;
      fieldEl.appendChild(el);
      setTimeout(() => el.remove(), 700);
    };
    add('hunt-shatter', '');
    const N = 12;
    for (let i = 0; i < N; i++) {
      const a = (i + Math.random() * 0.8) / N * Math.PI * 2, far = 40 + Math.random() * 44;
      add('hunt-shard', `--dx:${Math.round(Math.cos(a) * far)}px;--dy:${Math.round(Math.sin(a) * far)}px;--turn:${Math.round(Math.random() * 600 - 300)}deg;` +
        `--size:${(0.6 + Math.random() * 0.8).toFixed(2)};--tilt:${Math.round(Math.random() * 360)}deg`);
    }
  }

  function refreshNumbers() {
    const me = data.me, h = data.hunt;
    const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    set('#hunt-today', me.today);
    set('#hunt-today-sub', todaySub(me, h));
    set('#hunt-combo-n', me.combo);
    set('#hunt-combo-sub', comboSub(me, h));
    set('#hunt-season', fmt.credits(me.season));
    set('#hunt-all-time', fmt.credits(me.all_time));
    set('#hunt-picks', fmt.n0(me.picks));
    set('#hunt-bananas', fmt.n0(me.bananas));
    const combo = $('#hunt-combo');
    if (combo) { combo.innerHTML = comboHtml(); combo.dataset.mult = me.mult; }
    clearTimeout(comboTimer);
    if (me.combo) comboTimer = setTimeout(() => { if (data && data.me === me) { me.combo = 0; me.mult = 1; refreshNumbers(); } }, h.combo.idle_s * 1000);
  }

  function freeze(fieldEl, seconds) {
    frozenUntil = performance.now() + seconds * 1000;
    fieldEl.classList.add('hunt-frozen');
    setTimeout(() => { if (performance.now() >= frozenUntil - 20) fieldEl.classList.remove('hunt-frozen'); }, seconds * 1000);
  }

  // Take the server's answer to a click or a timer: the numbers, what to say about it, and the field.
  function apply(r, fieldEl, shown) {
    const me = data.me, was = target, lost = me.combo >= 2 && r.combo === 0;
    sound(r, lost, me.mult || 1);
    me.today = r.today; me.left = r.left; me.done = r.done; me.under_floor = r.under_floor;
    me.free = !!r.free;
    if (!r.free && !r.done) freePlay = false; // there are credits to pick again (a new day, or under the floor): back to the real thing
    me.combo = r.combo ?? 0; me.mult = r.mult ?? 1;
    const here = shown || (was ? layout().at(was) : { x: fieldEl.clientWidth / 2, y: fieldEl.clientHeight / 2 });
    if (r.hit) {
      session += 1;
      me.season += r.paid; me.all_time += r.paid;
      if (state.me && r.balance !== undefined) { state.me.balance = r.balance; renderMe(); }
      if (r.kind === 'boss') { // every wave stopped: the prize, on top of the day's cap
        pop(fieldEl, fieldEl.clientWidth / 2, fieldEl.clientHeight / 2, r.free ? 'Onkey is safe!' : `Onkey is safe! +${r.paid}`, 'gold');
        toast(r.free ? 'You beat the scientist.' : `You beat the scientist: +${r.paid} credits, on top of today's cap.`, 'good');
        window.FiveOnkey?.note('hunt_boss_won', { amount: r.paid });
      } else {
        if (!r.free) { me.bananas += 1; me.picks += 1; }
        const word = r.air ? 'Caught!' : r.kind === 'green' ? 'Ripe!' : ''; // playing on for nothing, there's no number to show
        pop(fieldEl, here.x, here.y, r.free ? word || 'Got it!' : `${word}${word ? ' ' : ''}+${r.paid}`, r.kind === 'golden' ? 'gold' : r.air ? 'air' : '');
      }
      if (r.swept && r.bunch_bonus && !r.free) pop(fieldEl, here.x, here.y - 34, `${r.kind === 'volley' ? 'Clean volley' : 'Whole bunch'}! +${r.bunch_bonus}`, 'air');
      if (r.found) { toast(`You found the hidden item: ${r.found.label}!`, 'good'); pop(fieldEl, here.x, here.y - 34, 'Hidden item!', 'gold'); window.FiveOnkey?.note('hunt_found', { label: r.found.label }); }
      if (session % 25 === 0) window.FiveOnkey?.note('hunt', { n: session, today: r.today });
    } else if (r.reason === 'rotten') { pop(fieldEl, here.x, here.y, 'Rotten!', 'bad'); freeze(fieldEl, r.frozen_s || data.hunt.freeze_s); }
    else if (r.reason === 'corn') { // corn: it costs credits and the combo, and Onkey has something to say
      pop(fieldEl, here.x, here.y, r.lost ? `Corn! −${r.lost}` : 'Corn!', 'bad');
      me.season -= r.lost || 0; me.all_time -= r.lost || 0;
      if (state.me && r.balance !== undefined) { state.me.balance = r.balance; renderMe(); }
      window.FiveOnkey?.note('hunt_corn', { lost: r.lost || 0 });
    } else if (r.reason === 'cracked') { // the ice breaks off it, on the ground or in the air
      pop(fieldEl, here.x, here.y, 'Crack!', 'air');
      const at = was && !flight ? layout().at(was) : here;
      shatter(fieldEl, at.x, at.y);
    }
    else if (r.reason === 'split') pop(fieldEl, here.x, here.y, 'Split!', 'air');
    else if (r.reason === 'unripe') pop(fieldEl, here.x, here.y, 'Hold it!', 'air');
    else if (r.reason === 'vine') pop(fieldEl, here.x, here.y, 'Drag it!', 'air');
    else if (r.reason === 'offbeat') { pop(fieldEl, here.x, here.y, 'Off beat!', 'air'); bongosReset(fieldEl); }
    else if (r.reason === 'bongo') pop(fieldEl, here.x, here.y, 'Tap the drums!', 'air');
    else if (r.reason === 'slipped') { pop(fieldEl, here.x, here.y, 'Slipped!', 'air'); vineHome(fieldEl); }
    else if (r.reason === 'corn_gone') pop(fieldEl, here.x, here.y, 'That was corn', 'air');
    else if (r.reason === 'rotted') pop(fieldEl, here.x, here.y, 'It rotted', 'bad');
    else if (r.reason === 'stolen') pop(fieldEl, here.x, here.y, 'Greg took it!', 'bad');
    else if (r.reason === 'shooed') { shooGreg(fieldEl); pop(fieldEl, here.x, here.y, 'Shoo!', 'air'); }
    if (lost) { const c = $('#hunt-combo', fieldEl); if (c) { c.classList.remove('broke'); void c.offsetWidth; c.classList.add('broke'); } }
    // Answers to clicks on several claws can come back out of order: within a fight a wave never goes back, and a
    // claw that was stopped stays stopped.
    if (r.target && was && r.target.kind === 'boss' && was.kind === 'boss' && r.target.id === was.id) {
      if (r.target.boss.wave < was.boss.wave) r.target = was;
      else if (r.target.boss.wave === was.boss.wave) r.target.boss.claws.forEach((c) => { if (was.boss.claws.some((o) => o.id === c.id && o.hit)) c.hit = true; });
    }
    target = r.target;
    if (r.done) { window.FiveOnkey?.note('hunt_done', { today: r.today }); clearField(fieldEl); draw(); return; }
    refreshNumbers();
    if (r.reason === 'boss_lost') { rescue(fieldEl); return; } // Man Strudel first, then the next banana
    if (!target) return;
    if (r.reason === 'split') { clearField(fieldEl); land(fieldEl, target); return; } // its pieces, where it was
    if (r.reason === 'cracked') { // on the ground it shows the crack now; cracked in the air, it flies on and lands cracked
      $$('.hunt-fly.picked', fieldEl).forEach((el) => el.classList.remove('picked'));
      const el = $('#hunt-banana', fieldEl); if (el) { el.classList.remove('picked'); el.classList.add('cracked'); }
      return;
    }
    if (target.kind === 'boss' && was && target.id === was.id) { bossSync(fieldEl, target); return; }
    if (!was || target.id !== was.id) throwTo(fieldEl, target); // Onkey throws the next one in
    else if (r.hit && r.kind === 'vine' && target.kind === 'volley') $$('.hunt-vine, #hunt-banana', fieldEl).forEach((el) => el.remove()); // a volley's vine banana, dragged
    else if (r.hit && (target.kind === 'bunch' || target.kind === 'volley')) target.items.forEach((it, i) => { if (it.picked) $$(`[data-i="${i}"]`, fieldEl).forEach((el) => el.remove()); });
    else if (!r.hit) $$('.hunt-banana.picked, .hunt-fly.picked', fieldEl).forEach((el) => el.classList.remove('picked')); // the click didn't count: what it hid is still there
  }

  // A timer on the field ran out (a golden banana, a bunch, Greg arriving): ask the server what became of it.
  async function nudge(fieldEl, t, tries = 0) {
    const still = () => target && target.id === t.id && document.body.contains(fieldEl); // the same thing, whatever was picked from it since
    if (!still()) return;
    try {
      const r = await post('/api/hunt/next');
      if (!still()) return;
      if (r.target && r.target.id === t.id && tries < 4) { timers.push(setTimeout(() => nudge(fieldEl, t, tries + 1), 350)); return; } // not yet, says the server
      apply(r, fieldEl);
    } catch (err) { /* the next click sorts it out */ }
  }

  async function send(body, fieldEl, shown) {
    if (busy) return;
    busy = true;
    try {
      const r = await post('/api/hunt/click', body);
      // The page may have been drawn again while the click was away: the answer belongs to the field that's up now
      // (clearing the old one would stop the new one's throw and leave its banana in Onkey's hand).
      const live = document.body.contains(fieldEl) ? fieldEl : $('#hunt-field');
      if (live) apply(r, live, live === fieldEl ? shown : undefined);
    } catch (err) {
      toast(err.message, 'bad');
      $$('.hunt-banana.picked', fieldEl).forEach((el) => el.classList.remove('picked'));
      vineHome(fieldEl);
    } finally {
      busy = false;
    }
  }

  // A click on the field at `shown` (its own pixels): on Greg, on the banana in the air, or on the ground.
  function clicked(shown, fieldEl, pointer) {
    if (busy || grip || !target || !state.me || !data.me || data.me.done || performance.now() < frozenUntil) return;
    const lay = layout(), h = data.hunt, reach = lay.phone ? TAP_R : h.field.r;
    const near = (p, r = reach) => Math.hypot(shown.x - p.x, shown.y - p.y) <= r;
    const greg = $('.hunt-greg:not(.shooed)', fieldEl);
    if (greg) {
      const box = greg.getBoundingClientRect(), f = fieldEl.getBoundingClientRect();
      if (near({ x: (box.left + box.width / 2 - f.left) / lay.k, y: (box.top + box.height / 2 - f.top) / lay.k }, 38)) { send({ x: 0, y: 0, shoo: true }, fieldEl, shown); return; }
    }
    if (target.kind === 'volley') { // steel bananas: each one by itself, and only once it's down
      const tail = vineOf(target), tailEl = tail && $('#hunt-banana:not(.picked)', fieldEl);
      if (tailEl && near(lay.at(tail))) { startDrag(fieldEl, tailEl, shown, pointer); return; } // the vine banana on the end of it
      for (const [i, it] of target.items.entries()) {
        const el = it.picked ? null : $(`.hunt-banana[data-i="${i}"]:not(.picked)`, fieldEl);
        if (!el || !near(lay.at(it))) continue;
        el.classList.add('picked');
        const at = lay.phone ? it : lay.toField(shown.x, shown.y);
        send({ x: Math.round(at.x), y: Math.round(at.y) }, fieldEl, shown);
        return;
      }
      const f = fieldEl.getBoundingClientRect(); // a click on one in the air only rings off it
      const up = $$('.hunt-fly.volley', fieldEl).some((el) => {
        const box = el.getBoundingClientRect();
        return near({ x: (box.left + box.width / 2 - f.left) / lay.k, y: (box.top + box.height / 2 - f.top) / lay.k }, lay.phone ? TAP_R + 10 : h.air.r);
      });
      if (up) { pop(fieldEl, shown.x, shown.y, 'Clang!', 'air'); sfx.clang(); }
      return;
    }
    if (flight) { // in the air: a click on it is a catch, anywhere else waits for it to land
      const k = airK();
      if (k === null || k < h.air.from || k > h.air.to) return;
      const spot = arcAt(target, k);
      if (!near(lay.at(spot), lay.phone ? TAP_R + 10 : h.air.r)) return;
      const at = lay.phone ? spot : lay.toField(shown.x, shown.y);
      $$('.hunt-fly', fieldEl).forEach((el) => { if (!el.classList.contains('rotten')) el.classList.add('picked'); });
      send({ x: Math.round(at.x), y: Math.round(at.y), air: Number(k.toFixed(3)) }, fieldEl, shown);
      return;
    }
    if (target.kind === 'bongos' && drums && drumAt(shown) !== null) { tapDrum(fieldEl, drumAt(shown)); return; } // a tap on a drum
    if ((target.kind === 'green' || target.kind === 'vine') && near(lay.at(target))) { // not a click: a hold, or a drag
      const el = $('#hunt-banana:not(.picked)', fieldEl);
      if (el) (target.kind === 'green' ? startHold : startDrag)(fieldEl, el, shown, pointer);
      return;
    }
    // On the ground: what the click is on, if anything (on a phone it's reported as that thing's own spot).
    const things = (target.kind === 'bunch' ? target.items.map((it, i) => ({ p: it, el: $(`.hunt-banana[data-i="${i}"]`, fieldEl), live: !it.picked })) : [{ p: target.kind === 'bouncy' && hop.id === target.id ? target.hops[hop.i] : target, el: $('#hunt-banana', fieldEl), live: true }])
      .concat(target.decoy ? [{ p: target.decoy, el: null, live: true }] : []);
    const on = things.find((x) => x.live && near(lay.at(x.p)));
    const stays = target.kind === 'split' || (target.kind === 'frozen' && !target.cracked); // the first click doesn't pick these
    if (on && on.el && !stays) on.el.classList.add('picked'); // it vanishes at once
    const at = on && lay.phone ? on.p : lay.toField(shown.x, shown.y);
    send({ x: Math.round(at.x), y: Math.round(at.y) }, fieldEl, shown);
  }

  function bind(viewEl) {
    const fieldEl = $('#hunt-field', viewEl);
    $('#hunt-free', viewEl)?.addEventListener('click', () => { // Keep playing: load the hunt again, this time for nothing
      freePlay = true;
      data = null;
      draw();
    });
    if (!fieldEl) return;
    frozenUntil = 0;
    const spotOf = (e) => { // where a pointer is, in the field's own pixels, whatever size it's drawn at
      const r = fieldEl.getBoundingClientRect(), k = layout().k;
      return { x: (e.clientX - r.left) / k, y: (e.clientY - r.top) / k };
    };
    fieldEl.addEventListener('pointerdown', (e) => {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      if (e.target.closest && e.target.closest('.hunt-sound')) return; // the speaker button isn't a miss
      if (target && target.kind === 'boss') { // the fight: only the claws take a click
        const claw = e.target.closest ? e.target.closest('.hunt-claw') : null;
        if (claw) hitClaw(claw, fieldEl);
        return;
      }
      clicked(spotOf(e), fieldEl, e.pointerId);
    });
    fieldEl.addEventListener('pointermove', (e) => { // a hold has to stay on its banana; a drag takes the banana along
      if (!grip || grip.done || grip.pointer !== e.pointerId) return;
      const q = spotOf(e);
      if (grip.kind === 'drag') dragTo(q);
      else if (Math.hypot(q.x - grip.home.x, q.y - grip.home.y) > grip.reach) letGo();
    });
    const up = (e) => { if (grip && grip.pointer === e.pointerId) letGo(); };
    fieldEl.addEventListener('pointerup', up);
    fieldEl.addEventListener('pointercancel', up);
    fieldEl.addEventListener('contextmenu', (e) => { // a long press on a phone is a hold here, not a menu
      if (grip || (e.target.closest && e.target.closest('.hunt-banana.green, .hunt-banana.vine'))) e.preventDefault();
    });
    fieldEl.addEventListener('keydown', (e) => { // each banana is a button: Enter or Space picks it
      const claw = e.target.closest ? e.target.closest('.hunt-claw') : null;
      if ((e.key === 'Enter' || e.key === ' ') && claw) { e.preventDefault(); hitClaw(claw, fieldEl); return; }
      const drum = e.target.closest ? e.target.closest('.hunt-bongo') : null;
      if ((e.key === 'Enter' || e.key === ' ') && drum) { e.preventDefault(); if (!e.repeat && !flight) tapDrum(fieldEl, Number(drum.dataset.drum)); return; }
      const b = e.target.closest ? e.target.closest('button.hunt-banana') : null;
      if ((e.key === 'Enter' || e.key === ' ') && b && grip) { e.preventDefault(); return; } // the key is still down
      if ((e.key === 'Enter' || e.key === ' ') && b && target && !flight && !busy && performance.now() >= frozenUntil) {
        e.preventDefault();
        if (target.kind === 'green' || b.classList.contains('vine')) { // held down, the key ripens the one and slides the other
          if (!e.repeat) (target.kind === 'green' ? startHold : startDrag)(fieldEl, b, layout().at(target.kind === 'green' ? target : vineOf(target)), null);
          return;
        }
        const p = b.dataset.i !== undefined ? target.items[Number(b.dataset.i)] : target;
        b.classList.add('picked');
        send({ x: p.x, y: p.y }, fieldEl, layout().at(p));
      }
    });
    fieldEl.addEventListener('keyup', (e) => { if ((e.key === 'Enter' || e.key === ' ') && grip && grip.pointer === null) letGo(); });
    bindSound(fieldEl);
    const begin = () => restart(fieldEl);
    if (document.documentElement.classList.contains('onkey-walking')) {
      // Onkey is still walking over from the logo (onkey.js): the first banana waits until he's in his corner.
      let begun = false;
      const go = () => {
        if (begun) return;
        begun = true;
        document.removeEventListener('onkey:seated', go);
        if (document.body.contains(fieldEl) && data && data.me && !data.me.done) begin();
      };
      document.addEventListener('onkey:seated', go);
      setTimeout(go, 8000); // he never keeps the hunt waiting for good
    } else if (target) throwTo(fieldEl, target); // the first banana of the visit comes in the same way
    else if (data && data.me && !data.me.done) begin(); // the server had nothing down: ask for a banana
  }

  return { init, load, view, bind };
})();
