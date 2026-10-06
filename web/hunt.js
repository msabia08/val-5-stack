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
 * - picks in a row build a combo (x2, x3) shown in the corner of the field; a miss breaks it;
 * - from Onkey's lore: the scientist's boss fight (`bossWave()`: his claws come for Onkey in waves), and Man Strudel visits
 *   (`visitStrudel()`), who takes nothing;
 * - one pick a day hides an item, and the field's scenery changes by the day (`hunt.theme`).
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

  async function load() {
    const name = state.me?.name || null;
    const next = await api('/api/hunt');
    if (name !== owner) { session = 0; target = null; }
    owner = name;
    data = next;
    if (next.me && !next.me.done) { // have the server put one down (or start the timers of the one that's there again)
      const r = await api('/api/hunt/start', { method: 'POST', body: '{}' });
      data.me = r.me;
    }
    target = data.me ? data.me.target : null;
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
    const label = { golden: 'Pick the golden banana', frozen: 'Crack the frozen banana, then pick it', bouncy: 'Pick the bouncing banana', split: 'Pick the banana' }[kind] || 'Pick the banana';
    if (kind === 'frozen' && p.cracked) kind = 'frozen cracked';
    return `<button type="button" class="hunt-banana ${kind} land"${i === undefined ? ' id="hunt-banana"' : ` data-i="${i}"`} style="${style}" aria-label="${label}">🍌</button>`;
  }

  const todaySub = (me, h) => (me.done ? 'done for today' : me.under_floor ? `past the cap: topping up to ${fmt.credits(h.floor)} credits` : 'credits picked');
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
        `If you drop under ${fmt.credits(h.floor)} credits before then, you can pick back up to ${fmt.credits(h.floor)}.</div></div>`;
    }
    // The banana isn't in the markup: bind() has Onkey throw it in.
    return `<div class="${cls}" id="hunt-field" style="${size}">${scenery()}<div class="hunt-combo" id="hunt-combo" aria-hidden="true">${comboHtml()}</div>` +
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
        `A <b>volley</b> is ${h.volley.size} <b>steel bananas</b> thrown one after another: they can't be caught in the air, only picked once they land. Pick every one in time for ${h.volley.bonus} more. <b>Greg</b> sometimes walks in to take a banana: pick it first, or click Greg to send him off. ` +
        `Now and then <b>the scientist</b> comes for Onkey himself: his claws come down in ${h.boss.waves.length} waves, each faster than the last, and you click every claw before it reaches Onkey. Stop them all for ${h.boss.prize} credits on top of the day's ${h.daily_max}; let one through and Man Strudel has to set Onkey free, and your combo is gone. Man Strudel himself only wants to say hello. ` +
        `Picks in a row build a <b>combo</b>: every banana pays double from ${h.combo.steps[0]} in a row and triple from ${h.combo.steps[1]}, until you miss, pick a rotten one, lose one to Greg, lose to the scientist, or stop for ${h.combo.idle_s} seconds. ` +
        `One of your picks each day also turns up a <b>hidden item</b>: shop bananas or a daily wheel token. ` +
        `The server places every banana and judges every click, and picks less than ${Math.round(h.min_interval_s * 1000)} ms apart on the ground aren't paid. The day's ${h.daily_max} credits turn over at midnight Pacific, like the daily wheel; ` +
        `the extras only get you there sooner, and only beating the scientist pays past it. Once you've had the day's ${h.daily_max} the hunt closes, with one exception, a top-up: if you have fewer than ${fmt.credits(h.floor)} credits you can pick until you have ${fmt.credits(h.floor)}, so nobody is stuck with nothing. ` +
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
    $$('.hunt-banana, .hunt-fly, .hunt-greg, .hunt-clock, .hunt-msg, .hunt-claw, .hunt-arm, .hunt-boss, .hunt-strudel', fieldEl).forEach((el) => el.remove());
    fieldEl.classList.remove('hunt-fight');
  }

  // Everything has landed: the things to pick, then whatever clock belongs to this target.
  function land(fieldEl, t) {
    flight = null;
    const h = data.hunt;
    if (t.kind === 'bunch') t.items.forEach((it, i) => { if (!it.picked) fieldEl.insertAdjacentHTML('beforeend', itemHtml(it, 'bunch', i)); });
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
      }, (n + 1) * h.bounce.hop_s * 1000)));
    }
    const ttl = t.kind === 'golden' ? h.gold.ttl_s : t.kind === 'bunch' ? h.bunch.ttl_s : 0;
    if (ttl) { // a bar across the top of the field runs down, then the server says what became of it
      fieldEl.insertAdjacentHTML('beforeend', `<div class="hunt-clock ${t.kind}" aria-hidden="true"><i style="animation-duration:${ttl}s"></i></div>`);
      if (t.kind === 'golden') timers.push(setTimeout(() => $('#hunt-banana', fieldEl)?.classList.add('rotting'), ttl * 1000 - 600));
      timers.push(setTimeout(() => nudge(fieldEl, t), ttl * 1000));
    }
    if (t.kind === 'corn') timers.push(setTimeout(() => nudge(fieldEl, t), h.corn.ttl_s * 1000)); // no clock: that would give it away
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
    if (`${t.id}:${t.boss.wave}` !== bossShown) { pop(fieldEl, fieldEl.clientWidth / 2, fieldEl.clientHeight / 2, 'Wave cleared!', 'air'); bossWave(fieldEl, t); return; }
    t.boss.claws.forEach((c) => { if (c.hit) $$(`[data-claw="${c.id}"]`, fieldEl).forEach((el) => el.remove()); });
  }
  function hitClaw(el, fieldEl) {
    if (el.classList.contains('hit')) return;
    el.classList.add('hit'); // it stops at once; the server confirms
    $(`.hunt-arm[data-claw="${el.dataset.claw}"]`, fieldEl)?.classList.add('hit');
    api('/api/hunt/click', { method: 'POST', body: JSON.stringify({ x: 0, y: 0, claw: Number(el.dataset.claw) }) })
      .then((r) => { if (document.body.contains(fieldEl)) apply(r, fieldEl); })
      .catch(() => $$(`[data-claw="${el.dataset.claw}"]`, fieldEl).forEach((x) => x.classList.remove('hit')));
  }
  // A wave's time is up on the page: ask the server whether a claw got there (it allows a little slack).
  async function bossTimeout(fieldEl, id, wave, tries = 0) {
    const mine = () => target && target.kind === 'boss' && target.id === id && target.boss.wave === wave && document.body.contains(fieldEl);
    if (!mine()) return;
    try {
      const r = await api('/api/hunt/next', { method: 'POST', body: '{}' });
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
    timers.push(setTimeout(() => restart(fieldEl), 3200));
  }
  // The server puts a banana down (or starts the timers of the one that's there again), and Onkey throws it in.
  function restart(fieldEl) {
    return api('/api/hunt/start', { method: 'POST', body: '{}' }).then((r) => {
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
    const lay = layout();
    const spots = t.kind === 'bunch' ? t.items.filter((it) => !it.picked).map((it) => ({ p: it, cls: 'bunch' })) : [{ p: t, cls: t.kind }];
    if (t.decoy) spots.push({ p: t.decoy, cls: t.decoy.kind || 'rotten' });
    let anim = null;
    spots.forEach(({ p, cls }) => {
      const a = fly(fieldEl, p, cls, lay, 0).anim;
      timers.push(a);
      if (!anim) anim = a;
    });
    flight = { t, anim, catchable: t.kind !== 'bunch' };
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
  // One thing in the air, from Onkey's hand along its arc to `p`, starting `wait` ms after the wind-up.
  function fly(fieldEl, p, cls, lay, wait) {
    const el = document.createElement('span');
    el.className = `hunt-fly ${cls}`;
    if (cls === 'corn') el.innerHTML = CORN_SVG; else el.textContent = '🍌';
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
  // each is picked once it's down, and when the last has landed a clock runs on what's left. Each one is made as it's
  // thrown, so the ones still to come don't sit in his hand.
  function throwVolley(fieldEl, t) {
    const lay = layout(), h = data.hunt, gap = h.volley.gap_s * 1000, onkey = $('#hunt-onkey', fieldEl);
    flight = null;
    t.items.forEach((it, i) => {
      if (it.picked) return;
      timers.push(setTimeout(() => {
        if (onkey) { onkey.classList.remove('throw'); void onkey.offsetWidth; onkey.classList.add('throw'); }
        const f = fly(fieldEl, it, 'volley', lay, 0);
        f.anim.onfinish = () => {
          f.el.remove();
          if (target && target.id === t.id && !target.items[i].picked) fieldEl.insertAdjacentHTML('beforeend', itemHtml(it, 'volley', i));
        };
        timers.push(f.anim);
      }, i * gap));
    });
    const down = THROW_DELAY + (t.items.length - 1) * gap + THROW_MS;
    timers.push(setTimeout(() => fieldEl.insertAdjacentHTML('beforeend', `<div class="hunt-clock bunch" aria-hidden="true"><i style="animation-duration:${h.volley.ttl_s}s"></i></div>`), down));
    timers.push(setTimeout(() => nudge(fieldEl, t), down + h.volley.ttl_s * 1000));
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
    me.today = r.today; me.left = r.left; me.done = r.done; me.under_floor = r.under_floor;
    me.combo = r.combo ?? 0; me.mult = r.mult ?? 1;
    const here = shown || (was ? layout().at(was) : { x: fieldEl.clientWidth / 2, y: fieldEl.clientHeight / 2 });
    if (r.hit) {
      session += 1;
      me.season += r.paid; me.all_time += r.paid;
      if (state.me && r.balance !== undefined) { state.me.balance = r.balance; renderMe(); }
      if (r.kind === 'boss') { // every wave stopped: the prize, on top of the day's cap
        pop(fieldEl, fieldEl.clientWidth / 2, fieldEl.clientHeight / 2, `Onkey is safe! +${r.paid}`, 'gold');
        toast(`You beat the scientist: +${r.paid} credits, on top of today's cap.`, 'good');
        window.FiveOnkey?.note('hunt_boss_won', { amount: r.paid });
      } else {
        me.bananas += 1; me.picks += 1;
        pop(fieldEl, here.x, here.y, `${r.air ? 'Caught! ' : ''}+${r.paid}`, r.kind === 'golden' ? 'gold' : r.air ? 'air' : '');
      }
      if (r.swept && r.bunch_bonus) pop(fieldEl, here.x, here.y - 34, `${r.kind === 'volley' ? 'Clean volley' : 'Whole bunch'}! +${r.bunch_bonus}`, 'air');
      if (r.found) { toast(`You found the hidden item: ${r.found.label}!`, 'good'); pop(fieldEl, here.x, here.y - 34, 'Hidden item!', 'gold'); window.FiveOnkey?.note('hunt_found', { label: r.found.label }); }
      if (session % 25 === 0) window.FiveOnkey?.note('hunt', { n: session, today: r.today });
    } else if (r.reason === 'rotten') { pop(fieldEl, here.x, here.y, 'Rotten!', 'bad'); freeze(fieldEl, r.frozen_s || data.hunt.freeze_s); }
    else if (r.reason === 'corn') { // corn: it costs credits and the combo, and Onkey has something to say
      pop(fieldEl, here.x, here.y, r.lost ? `Corn! −${r.lost}` : 'Corn!', 'bad');
      me.season -= r.lost || 0; me.all_time -= r.lost || 0;
      if (state.me && r.balance !== undefined) { state.me.balance = r.balance; renderMe(); }
      window.FiveOnkey?.note('hunt_corn', { lost: r.lost || 0 });
    } else if (r.reason === 'cracked') pop(fieldEl, here.x, here.y, 'Crack!', 'air');
    else if (r.reason === 'split') pop(fieldEl, here.x, here.y, 'Split!', 'air');
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
    else if (r.hit && (target.kind === 'bunch' || target.kind === 'volley')) target.items.forEach((it, i) => { if (it.picked) $$(`[data-i="${i}"]`, fieldEl).forEach((el) => el.remove()); });
    else if (!r.hit) $$('.hunt-banana.picked, .hunt-fly.picked', fieldEl).forEach((el) => el.classList.remove('picked')); // the click didn't count: what it hid is still there
  }

  // A timer on the field ran out (a golden banana, a bunch, Greg arriving): ask the server what became of it.
  async function nudge(fieldEl, t, tries = 0) {
    const still = () => target && target.id === t.id && document.body.contains(fieldEl); // the same thing, whatever was picked from it since
    if (!still()) return;
    try {
      const r = await api('/api/hunt/next', { method: 'POST', body: '{}' });
      if (!still()) return;
      if (r.target && r.target.id === t.id && tries < 4) { timers.push(setTimeout(() => nudge(fieldEl, t, tries + 1), 350)); return; } // not yet, says the server
      apply(r, fieldEl);
    } catch (err) { /* the next click sorts it out */ }
  }

  async function send(body, fieldEl, shown) {
    if (busy) return;
    busy = true;
    try {
      const r = await api('/api/hunt/click', { method: 'POST', body: JSON.stringify(body) });
      // The page may have been drawn again while the click was away: the answer belongs to the field that's up now
      // (clearing the old one would stop the new one's throw and leave its banana in Onkey's hand).
      const live = document.body.contains(fieldEl) ? fieldEl : $('#hunt-field');
      if (live) apply(r, live, live === fieldEl ? shown : undefined);
    } catch (err) {
      toast(err.message, 'bad');
      $$('.hunt-banana.picked', fieldEl).forEach((el) => el.classList.remove('picked'));
    } finally {
      busy = false;
    }
  }

  // A click on the field at `shown` (its own pixels): on Greg, on the banana in the air, or on the ground.
  function clicked(shown, fieldEl) {
    if (busy || !target || !state.me || !data.me || data.me.done || performance.now() < frozenUntil) return;
    const lay = layout(), h = data.hunt, reach = lay.phone ? TAP_R : h.field.r;
    const near = (p, r = reach) => Math.hypot(shown.x - p.x, shown.y - p.y) <= r;
    const greg = $('.hunt-greg:not(.shooed)', fieldEl);
    if (greg) {
      const box = greg.getBoundingClientRect(), f = fieldEl.getBoundingClientRect();
      if (near({ x: (box.left + box.width / 2 - f.left) / lay.k, y: (box.top + box.height / 2 - f.top) / lay.k }, 38)) { send({ x: 0, y: 0, shoo: true }, fieldEl, shown); return; }
    }
    if (target.kind === 'volley') { // steel bananas: each one by itself, and only once it's down
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
      if (up) pop(fieldEl, shown.x, shown.y, 'Clang!', 'air');
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
    if (!fieldEl) return;
    frozenUntil = 0;
    fieldEl.addEventListener('pointerdown', (e) => {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      if (target && target.kind === 'boss') { // the fight: only the claws take a click
        const claw = e.target.closest ? e.target.closest('.hunt-claw') : null;
        if (claw) hitClaw(claw, fieldEl);
        return;
      }
      const r = fieldEl.getBoundingClientRect(), k = layout().k; // the field's own pixels, whatever size it's drawn at
      clicked({ x: (e.clientX - r.left) / k, y: (e.clientY - r.top) / k }, fieldEl);
    });
    fieldEl.addEventListener('keydown', (e) => { // each banana is a button: Enter or Space picks it
      const claw = e.target.closest ? e.target.closest('.hunt-claw') : null;
      if ((e.key === 'Enter' || e.key === ' ') && claw) { e.preventDefault(); hitClaw(claw, fieldEl); return; }
      const b = e.target.closest ? e.target.closest('button.hunt-banana') : null;
      if ((e.key === 'Enter' || e.key === ' ') && b && target && !flight && !busy && performance.now() >= frozenUntil) {
        e.preventDefault();
        const p = b.dataset.i !== undefined ? target.items[Number(b.dataset.i)] : target;
        b.classList.add('picked');
        send({ x: p.x, y: p.y }, fieldEl, layout().at(p));
      }
    });
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
