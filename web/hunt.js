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
 * - a banana can be caught in the air for double (a click on it mid-flight is sent with `air`, how far along it was);
 * - a golden banana pays more but rots a few seconds after it lands; a bunch is five at once with a timer and a bonus
 *   for sweeping them; a rotten decoy next to the real one freezes you; Greg walks in to take a banana unless you pick
 *   it first or click him away. When one of those timers runs out the page asks /api/hunt/next what happened;
 * - picks in a row build a combo (x2, x3) shown in the corner of the field; a miss breaks it;
 * - from Onkey's lore: the scientist's claw comes down for a banana (`dropClaw()`), and Man Strudel visits
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
  const FIT = { page: 32, side: 296, card: 38, above: 150, below: 86, min: 0.6, max: 1.6 };
  function fit(f) {
    const w = document.documentElement.clientWidth - FIT.page - (state.me ? FIT.side : 0) - FIT.card;
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
    const label = kind === 'golden' ? 'Pick the golden banana' : 'Pick the banana';
    return `<button type="button" class="hunt-banana ${kind} land"${i === undefined ? ' id="hunt-banana"' : ` data-i="${i}"`} style="${style}" aria-label="${label}">🍌</button>`;
  }

  function liveLine() {
    const me = data && data.me, h = data && data.hunt;
    if (!me) return '';
    if (me.done) return `That's today's ${h.daily_max} credits. The hunt reopens at ${resetAt(me)}. Drop under ${fmt.credits(h.floor)} credits before then and you can pick back up to ${fmt.credits(h.floor)}.`;
    if (me.under_floor) return `You've had today's ${h.daily_max} credits. This is a top-up: ${plural(me.left, 'more credit')}, until you have ${fmt.credits(h.floor)}.`;
    return `${plural(me.left, 'credit')} left of today's ${h.daily_max}.`;
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
        ${kpi('This season', `<span id="hunt-season">${fmt.credits(me.season)}</span>`, `${fmt.credits(me.all_time)} credits all time`)}
        ${kpi('Hunt reopens', resetAt(me), `today's field: ${THEME_NAME[themeOf()]}`)}
      </section>` : '';
    const card = `<section class="card hunt-card"><h2>Banana Hunt</h2>
      ${how(`Onkey throws bananas into the field. Pick one for ${plural(h.per_banana, 'credit')}, or catch it in the air for double, up to ${h.daily_max} credits a day.`,
        `<b>Golden bananas</b> pay ${h.gold.value} but rot ${h.gold.ttl_s} seconds after they land. A <b>bunch</b> is ${h.bunch.size} at once: sweep them all inside ${h.bunch.ttl_s} seconds for ${h.bunch.bonus} more. ` +
        `A brown, <b>rotten banana</b> sometimes lands beside the real one: pick it and you can't pick anything for ${h.freeze_s} seconds. <b>Greg</b> sometimes walks in to take a banana: pick it first, or click Greg to send him off. ` +
        `The scientist's <b>claw</b> sometimes comes down for one: it can't be sent off, so pick the banana before it gets there. Man Strudel only wants to say hello. ` +
        `Picks in a row build a <b>combo</b>: every banana pays double from ${h.combo.steps[0]} in a row and triple from ${h.combo.steps[1]}, until you miss, pick a rotten one, lose one to Greg or the claw, or stop for ${h.combo.idle_s} seconds. ` +
        `One of your picks each day also turns up a <b>hidden item</b>: shop bananas or a daily wheel token. ` +
        `The server places every banana and judges every click, and picks less than ${Math.round(h.min_interval_s * 1000)} ms apart on the ground aren't paid. The day's ${h.daily_max} credits turn over at midnight Pacific, like the daily wheel; ` +
        `the extras only get you there sooner, and nothing pays past it. Once you've had the day's ${h.daily_max} the hunt closes, with one exception, a top-up: if you have fewer than ${fmt.credits(h.floor)} credits you can pick until you have ${fmt.credits(h.floor)}, so nobody is stuck with nothing. ` +
        'Credits from the hunt show in their own column on Standings and stay out of betting profit, like game rewards.')}
      <div class="hunt-stage"><div class="hunt-fit" style="${fitStyle(layout())}"><div class="hunt-zoom" style="${zoomStyle(layout())}">${field()}</div></div></div>
      <p class="muted small" id="hunt-live" aria-live="polite">${liveLine()}</p></section>`;
    // Your four tiles stand in a column to the right of the field. Signed out there are none, and the card has the page.
    return `${tiles ? `<div class="hunt-layout">${card}<aside class="hunt-side">${tiles}</aside></div>` : card}${SHOW_BOARD ? boardCard() : ''}`;
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
    $$('.hunt-banana, .hunt-fly, .hunt-greg, .hunt-clock, .hunt-msg, .hunt-claw, .hunt-strudel', fieldEl).forEach((el) => el.remove());
  }

  // Everything has landed: the things to pick, then whatever clock belongs to this target.
  function land(fieldEl, t) {
    flight = null;
    const h = data.hunt;
    if (t.kind === 'bunch') t.items.forEach((it, i) => { if (!it.picked) fieldEl.insertAdjacentHTML('beforeend', itemHtml(it, 'bunch', i)); });
    else fieldEl.insertAdjacentHTML('beforeend', itemHtml(t, t.kind));
    if (t.decoy) fieldEl.insertAdjacentHTML('beforeend', itemHtml(t.decoy, 'rotten'));
    const ttl = t.kind === 'golden' ? h.gold.ttl_s : t.kind === 'bunch' ? h.bunch.ttl_s : 0;
    if (ttl) { // a bar across the top of the field runs down, then the server says what became of it
      fieldEl.insertAdjacentHTML('beforeend', `<div class="hunt-clock ${t.kind}" aria-hidden="true"><i style="animation-duration:${ttl}s"></i></div>`);
      if (t.kind === 'golden') timers.push(setTimeout(() => $('#hunt-banana', fieldEl)?.classList.add('rotting'), ttl * 1000 - 600));
      timers.push(setTimeout(() => nudge(fieldEl, t), ttl * 1000));
    }
    if (t.greg) walkGreg(fieldEl, t);
    if (t.claw) dropClaw(fieldEl, t);
    if (t.strudel) visitStrudel(fieldEl, t);
  }

  // The scientist's claw comes down on a cable from the top of the field, his face at the top of it; when it
  // reaches the banana, it has it. It can't be shooed: pick the banana first.
  function dropClaw(fieldEl, t) {
    const to = layout().at(t);
    const claw = document.createElement('div');
    claw.className = 'hunt-claw';
    claw.setAttribute('aria-hidden', 'true');
    claw.style.left = `${to.x}px`;
    claw.innerHTML = '<img src="/assets/scientist-face.png" alt=""><i></i><b>🪝</b>';
    fieldEl.appendChild(claw);
    if (!claw.animate) return;
    const drop = claw.animate([{ height: '34px' }, { height: `${Math.max(40, to.y + 6)}px` }], { duration: data.hunt.claw_s * 1000, easing: 'ease-in', fill: 'both' });
    drop.onfinish = () => nudge(fieldEl, t);
    timers.push(drop);
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
    clearField(fieldEl);
    if (!t) return;
    if (!('animate' in Element.prototype)) { land(fieldEl, t); return; }
    const onkey = $('#hunt-onkey', fieldEl);
    if (onkey) { onkey.classList.remove('throw'); void onkey.offsetWidth; onkey.classList.add('throw'); }
    const lay = layout();
    const spots = t.kind === 'bunch' ? t.items.filter((it) => !it.picked).map((it) => ({ p: it, cls: 'bunch' })) : [{ p: t, cls: t.kind }];
    if (t.decoy) spots.push({ p: t.decoy, cls: 'rotten' });
    let anim = null;
    spots.forEach(({ p, cls }) => {
      const fly = document.createElement('span');
      fly.className = `hunt-fly ${cls}`;
      fly.textContent = '🍌';
      fly.setAttribute('aria-hidden', 'true');
      fieldEl.appendChild(fly);
      const frames = [], N = 30;
      for (let i = 0; i <= N; i++) {
        const k = i / N, at = lay.at(arcAt(p, k));
        frames.push({ offset: k, transform: `translate(${at.x.toFixed(1)}px, ${at.y.toFixed(1)}px) translate(-50%, -50%) rotate(${Math.round(k * 540)}deg) scale(${(1 + 0.35 * Math.sin(Math.PI * k)).toFixed(3)})` });
      }
      fly.style.transform = frames[0].transform; // in Onkey's hand during the wind-up, not at the field's corner
      const a = fly.animate(frames, { duration: THROW_MS, delay: THROW_DELAY, easing: 'linear', fill: 'both' });
      timers.push(a);
      if (!anim) anim = a;
    });
    flight = { t, anim, catchable: t.kind !== 'bunch' };
    let landed = false;
    const finish = () => { // once, whether the animation finishes or never reports back (a hidden tab)
      if (landed) return;
      landed = true;
      $$('.hunt-fly', fieldEl).forEach((el) => el.remove());
      if (target === t) land(fieldEl, t); else flight = null;
    };
    timers.push(setTimeout(finish, THROW_DELAY + THROW_MS + 600));
    anim.onfinish = finish;
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
    set('#hunt-live', liveLine());
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
      me.season += r.paid; me.all_time += r.paid; me.bananas += 1; me.picks += 1;
      if (state.me && r.balance !== undefined) { state.me.balance = r.balance; renderMe(); }
      pop(fieldEl, here.x, here.y, `${r.air ? 'Caught! ' : ''}+${r.paid}`, r.kind === 'golden' ? 'gold' : r.air ? 'air' : '');
      if (r.swept) pop(fieldEl, here.x, here.y - 34, `Whole bunch! +${r.bunch_bonus}`, 'air');
      if (r.found) { toast(`You found the hidden item: ${r.found.label}!`, 'good'); pop(fieldEl, here.x, here.y - 34, 'Hidden item!', 'gold'); window.FiveOnkey?.note('hunt_found', { label: r.found.label }); }
      if (session % 25 === 0) window.FiveOnkey?.note('hunt', { n: session, today: r.today });
    } else if (r.reason === 'rotten') { pop(fieldEl, here.x, here.y, 'Rotten!', 'bad'); freeze(fieldEl, r.frozen_s || data.hunt.freeze_s); }
    else if (r.reason === 'rotted') pop(fieldEl, here.x, here.y, 'It rotted', 'bad');
    else if (r.reason === 'stolen') pop(fieldEl, here.x, here.y, 'Greg took it!', 'bad');
    else if (r.reason === 'clawed') { pop(fieldEl, here.x, here.y, 'Ze claw has it!', 'bad'); window.FiveOnkey?.note('hunt_claw'); }
    else if (r.reason === 'shooed') { shooGreg(fieldEl); pop(fieldEl, here.x, here.y, 'Shoo!', 'air'); }
    if (lost) { const c = $('#hunt-combo', fieldEl); if (c) { c.classList.remove('broke'); void c.offsetWidth; c.classList.add('broke'); } }
    target = r.target;
    if (r.done) { window.FiveOnkey?.note('hunt_done', { today: r.today }); clearField(fieldEl); draw(); return; }
    refreshNumbers();
    if (!target) return;
    if (!was || target.id !== was.id) throwTo(fieldEl, target); // Onkey throws the next one in
    else if (r.hit && target.kind === 'bunch') target.items.forEach((it, i) => { if (it.picked) $(`.hunt-banana[data-i="${i}"]`, fieldEl)?.remove(); });
  }

  // A timer on the field ran out (a golden banana, a bunch, Greg arriving): ask the server what became of it.
  async function nudge(fieldEl, t, tries = 0) {
    if (target !== t || !document.body.contains(fieldEl)) return;
    try {
      const r = await api('/api/hunt/next', { method: 'POST', body: '{}' });
      if (target !== t) return;
      if (r.target && r.target.id === t.id && tries < 4) { timers.push(setTimeout(() => nudge(fieldEl, t, tries + 1), 350)); return; } // not yet, says the server
      apply(r, fieldEl);
    } catch (err) { /* the next click sorts it out */ }
  }

  async function send(body, fieldEl, shown) {
    if (busy) return;
    busy = true;
    try {
      const r = await api('/api/hunt/click', { method: 'POST', body: JSON.stringify(body) });
      apply(r, fieldEl, shown);
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
    const things = (target.kind === 'bunch' ? target.items.map((it, i) => ({ p: it, el: $(`.hunt-banana[data-i="${i}"]`, fieldEl), live: !it.picked })) : [{ p: target, el: $('#hunt-banana', fieldEl), live: true }])
      .concat(target.decoy ? [{ p: target.decoy, el: null, live: true }] : []);
    const on = things.find((x) => x.live && near(lay.at(x.p)));
    if (on && on.el) on.el.classList.add('picked'); // it vanishes at once
    const at = on && lay.phone ? on.p : lay.toField(shown.x, shown.y);
    send({ x: Math.round(at.x), y: Math.round(at.y) }, fieldEl, shown);
  }

  function bind(viewEl) {
    const fieldEl = $('#hunt-field', viewEl);
    if (!fieldEl) return;
    frozenUntil = 0;
    fieldEl.addEventListener('pointerdown', (e) => {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      const r = fieldEl.getBoundingClientRect(), k = layout().k; // the field's own pixels, whatever size it's drawn at
      clicked({ x: (e.clientX - r.left) / k, y: (e.clientY - r.top) / k }, fieldEl);
    });
    fieldEl.addEventListener('keydown', (e) => { // each banana is a button: Enter or Space picks it
      const b = e.target.closest ? e.target.closest('button.hunt-banana') : null;
      if ((e.key === 'Enter' || e.key === ' ') && b && target && !flight && !busy && performance.now() >= frozenUntil) {
        e.preventDefault();
        const p = b.dataset.i !== undefined ? target.items[Number(b.dataset.i)] : target;
        b.classList.add('picked');
        send({ x: p.x, y: p.y }, fieldEl, layout().at(p));
      }
    });
    // The server puts a banana down (or starts the timers of the one that's there again), and Onkey throws it in.
    const begin = () => api('/api/hunt/start', { method: 'POST', body: '{}' }).then((r) => {
      if (!document.body.contains(fieldEl)) return;
      data.me = r.me; target = r.me.target; throwTo(fieldEl, target);
    }).catch(() => {});
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
