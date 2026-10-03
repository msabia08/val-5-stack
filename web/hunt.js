/* 5-Stack Tracker: the Banana Hunt (Betting › Banana Hunt; data from /api/hunt, picks from /api/hunt/click, both in
 * fivestack/hunt.py).
 *
 * Onkey dropped his bananas all over the field. The server puts one down, the page draws it, and every click that
 * lands on it pays a credit and moves it somewhere else (the server judges each click and picks each spot, so the page
 * only reports where you clicked). Misses leave it where it is; picks faster than the server pays aren't paid; there's
 * a daily cap that turns over at midnight Pacific, like the daily wheel, which doesn't apply while you're under the
 * floor (250 credits). A pick vanishes the banana at once, floats a "+1" where you clicked and counts the credits chip
 * up; then Onkey, in his corner, winds up and throws the next banana in along an arc (`throwTo()`, the Web Animations
 * API, THROW_MS), and it can't be picked until it lands. Reduced motion puts it straight down. The whole page is only
 * redrawn when the hunt closes for the day; everything else is updated in place so the hunt stays snappy.
 *
 * FiveHunt.init(ctx) gets app.js's helpers; load(), view() and bind() are called like the other pages'.
 * Plain JS, no dependencies; loaded before app.js.
 */
window.FiveHunt = (() => {
  'use strict';

  let state, $, api, draw, esc, fmt, kpi, renderMe, toast, plainName;
  let data = null, owner = undefined;
  let target = null; // the banana to draw: {x, y} in field pixels, or null
  let busy = false; // a click is on its way to the server
  let flying = false; // Onkey's throw is in the air: nothing to pick yet
  let session = 0; // bananas picked since the page was opened
  const THROW_MS = 750; // how long a banana is in the air (the server won't pay a pick sooner than most of this)
  const how = (summary, more) => `<details class="how"><summary>${summary}</summary><div class="how-body">${more}</div></details>`;
  const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  // The jungle: fixed scenery so the field looks the same every visit.
  const SCENERY = [['🌴', 6, 14], ['🌿', 22, 88], ['🌴', 58, 10], ['🪨', 40, 92], ['🌿', 82, 20], ['🌴', 93, 84], ['🌱', 50, 50], ['🍃', 70, 62]];

  function init(ctx) { ({ state, $, api, draw, esc, fmt, kpi, renderMe, toast, plainName } = ctx); }

  // On a phone the server's field (1200 wide, 600 tall) doesn't fit, so the page draws it upright: the server's x
  // runs down the screen and its y across, each scaled to a field as wide as the screen and about a screen tall.
  // `at()` is where a field point is drawn. A tap is judged in screen pixels (within TAP_R of the banana as drawn,
  // about a fingertip) and reported to the server as the banana's own spot, as picking it with the keyboard is; a
  // tap anywhere else is reported where it fell, which the server calls a miss. The desktop draws the field as it is.
  const TAP_R = 30;
  const onPhone = () => matchMedia('(max-width: 640px)').matches;
  function layout() {
    const f = data.hunt.field;
    if (!onPhone()) return { phone: false, w: f.w, h: f.h, at: (t) => ({ x: t.x, y: t.y }), toField: (x, y) => ({ x, y }) };
    const w = Math.min(f.h, document.documentElement.clientWidth - 42); // the page's and the card's padding either side
    const h = Math.min(f.w, Math.max(320, window.innerHeight - 130)); // under the top bar, with the live line below
    const kx = w / f.h, ky = h / f.w;
    return { phone: true, w, h, at: (t) => ({ x: t.y * kx, y: t.x * ky }), toField: (x, y) => ({ x: y / ky, y: x / kx }) };
  }

  async function load() {
    const name = state.me?.name || null;
    const next = await api('/api/hunt');
    if (name !== owner) { session = 0; target = null; }
    owner = name;
    data = next;
    if (next.me && !next.me.done && !next.me.target) { // nothing down yet (first visit, or the server restarted)
      const r = await api('/api/hunt/start', { method: 'POST', body: '{}' });
      data.me = r.me;
    }
    target = data.me ? data.me.target : null;
  }

  // ---- drawing ----------------------------------------------------------------------------------------------------
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const resetAt = (me) => fmt.date(me.resets_ts * 1000);

  function scenery() {
    return SCENERY.map(([e, x, y]) => `<span class="hunt-deco" style="left:${x}%;top:${y}%" aria-hidden="true">${e}</span>`).join('');
  }
  const bananaHtml = (t) => { const p = layout().at(t); return `<button type="button" class="hunt-banana land" id="hunt-banana" style="left:${p.x}px;top:${p.y}px" aria-label="Pick the banana">🍌</button>`; };

  function liveLine() {
    const me = data && data.me, h = data && data.hunt;
    if (!me) return '';
    if (me.done) return `Onkey has all ${h.daily_max} bananas he wanted today. The hunt reopens at ${resetAt(me)}.`;
    if (me.under_floor) return `Past today's ${h.daily_max}, but Onkey won't let you starve: ${plural(me.left, 'more banana')} until you have ${fmt.credits(h.floor)} credits.`;
    return `${plural(me.left, 'banana')} left today. Each one is ${plural(h.per_banana, 'credit')}.`;
  }
  const todaySub = (me, h) => (me.done ? 'done for today' : me.under_floor ? `over the cap, but under ${fmt.credits(h.floor)} credits` : `${h.per_banana} credit each`);

  function field() {
    const h = data.hunt, me = data.me, lay = layout();
    const size = `width:${lay.w}px;height:${lay.h}px`;
    if (!me) return `<div class="hunt-field hunt-locked" style="${size}"><div class="hunt-msg"><a href="#" data-signin>Sign in</a> to hunt bananas for Onkey. Every one you pick is a credit.</div></div>`;
    if (me.done) {
      return `<div class="hunt-field hunt-locked" style="${size}">${scenery()}<img class="hunt-onkey full" src="/assets/onkey.png" alt="" aria-hidden="true">` +
        `<div class="hunt-msg"><b>Onkey is full.</b> ${h.daily_max} bananas today, ${fmt.credits(h.daily_max * h.per_banana)} credits. The hunt reopens at ${resetAt(me)} (midnight Pacific).</div></div>`;
    }
    // The banana isn't in the markup: bind() has Onkey throw it in.
    return `<div class="hunt-field" id="hunt-field" style="${size}">${scenery()}<img class="hunt-onkey" id="hunt-onkey" src="/assets/onkey.png" alt="" aria-hidden="true"></div>`;
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
        ${kpi('This sitting', `<span id="hunt-session">${session}</span>`, 'bananas since you opened the page')}
        ${kpi('This season', `<span id="hunt-season">${fmt.credits(me.season)}</span>`, `${fmt.credits(me.all_time)} credits all time`)}
        ${kpi('Hunt reopens', resetAt(me), 'your time; the day turns at midnight Pacific, like the wheel')}
      </section>` : '';
    return `${tiles}<section class="card hunt-card"><h2>Banana Hunt</h2>
      ${how(`Onkey dropped his bananas all over the field. Click one to pick it up: ${plural(h.per_banana, 'credit')} each, up to ${h.daily_max} a day, and Onkey throws the next one in.`,
        `The server places every banana and judges every click, so only real picks count: a click that lands on the banana pays ${plural(h.per_banana, 'credit')}, and Onkey throws the next one at least a hop away ` +
        `(it can't be picked until it lands); a miss leaves it where it is, and picks less than ${Math.round(h.min_interval_s * 1000)} ms apart aren't paid. The day's ${h.daily_max} turn over at midnight Pacific, like the daily wheel, ` +
        `except that with fewer than ${fmt.credits(h.floor)} credits the cap doesn't apply: you keep picking until you have ${fmt.credits(h.floor)}, so nobody is stuck broke. ` +
        'Credits from the hunt show in their own column on Standings and stay out of betting profit, like game rewards.')}
      <div class="hunt-stage">${field()}</div>
      <p class="muted small" id="hunt-live" aria-live="polite">${liveLine()}</p></section>
      ${boardCard()}`;
  }

  // ---- the throw --------------------------------------------------------------------------------------------------
  // Onkey winds up in his corner and the banana flies along an arc to where the server put it, spinning, and lands
  // with a squash. Only then is there a banana to pick.
  function land(fieldEl, t) {
    flying = false;
    $('#hunt-banana', fieldEl)?.remove();
    if (t) fieldEl.insertAdjacentHTML('beforeend', bananaHtml(t));
  }

  function throwTo(fieldEl, t) {
    $('#hunt-banana', fieldEl)?.remove();
    $('.hunt-msg', fieldEl)?.remove();
    if (!t) { flying = false; return; }
    if (reducedMotion() || !('animate' in Element.prototype)) { land(fieldEl, t); return; }
    flying = true;
    const onkey = $('#hunt-onkey', fieldEl);
    if (onkey) { onkey.classList.remove('throw'); void onkey.offsetWidth; onkey.classList.add('throw'); }
    const lay = layout(), to = lay.at(t);
    const x0 = fieldEl.clientWidth - (lay.phone ? 66 : 96), y0 = fieldEl.clientHeight - (lay.phone ? 72 : 104); // Onkey's hand, in the bottom-right corner
    const fly = document.createElement('span');
    fly.className = 'hunt-fly';
    fly.textContent = '🍌';
    fly.setAttribute('aria-hidden', 'true');
    fieldEl.appendChild(fly);
    const dist = Math.hypot(to.x - x0, to.y - y0), arc = Math.max(lay.phone ? 60 : 110, Math.min(240, dist * 0.4));
    const frames = [], N = 30;
    for (let i = 0; i <= N; i++) {
      const k = i / N, x = x0 + (to.x - x0) * k, y = y0 + (to.y - y0) * k - arc * 4 * k * (1 - k);
      frames.push({ offset: k, transform: `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) translate(-50%, -50%) rotate(${Math.round(k * 540)}deg) scale(${(1 + 0.35 * Math.sin(Math.PI * k)).toFixed(3)})` });
    }
    fly.style.transform = frames[0].transform; // in Onkey's hand during the wind-up, not at the field's corner
    const anim = fly.animate(frames, { duration: THROW_MS, delay: 120, easing: 'linear', fill: 'both' });
    let landed = false;
    const finish = () => { // once, whether the animation finishes, is cancelled, or never reports back (a hidden tab)
      if (landed) return;
      landed = true;
      clearTimeout(guard);
      fly.remove();
      if (target === t) land(fieldEl, t); else flying = false;
    };
    const guard = setTimeout(finish, THROW_MS + 600);
    anim.onfinish = finish;
    anim.oncancel = finish;
  }

  function pop(fieldEl, x, y, text) {
    if (reducedMotion()) return;
    const el = document.createElement('span');
    el.className = 'hunt-pop';
    el.textContent = text;
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
    fieldEl.appendChild(el);
    setTimeout(() => el.remove(), 750);
  }

  function refreshNumbers() {
    const me = data.me;
    const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    set('#hunt-today', me.today);
    set('#hunt-today-sub', todaySub(me, data.hunt));
    set('#hunt-session', session);
    set('#hunt-season', fmt.credits(me.season));
    set('#hunt-live', liveLine());
  }

  // x, y: the click in field pixels (what the server judges); `shown`: where it was on the page, for the "+1".
  async function pick(x, y, fieldEl, shown = layout().at({ x, y })) {
    if (busy || flying || !target || !state.me || !data.me || data.me.done) return;
    busy = true;
    const b = $('#hunt-banana', fieldEl);
    const h = data.hunt.field;
    if (b && Math.hypot(x - target.x, y - target.y) <= h.r) b.classList.add('picked'); // it vanishes at once
    try {
      const r = await api('/api/hunt/click', { method: 'POST', body: JSON.stringify({ x: Math.round(x), y: Math.round(y) }) });
      const me = data.me;
      me.today = r.today; me.left = r.left; me.done = r.done; me.under_floor = r.under_floor;
      if (r.hit) {
        session += 1;
        me.season += r.paid; me.all_time += r.paid; me.bananas += 1;
        if (state.me) { state.me.balance = r.balance; renderMe(); }
        pop(fieldEl, shown.x, shown.y, `+${r.paid}`);
        if (session % 25 === 0) window.FiveOnkey?.note('hunt', { n: session, today: r.today });
      }
      target = r.target;
      if (r.done) {
        window.FiveOnkey?.note('hunt_done', { today: r.today });
        busy = false;
        draw();
        return;
      }
      if (r.hit) throwTo(fieldEl, target); // Onkey throws the next one in; a miss leaves the banana where it is
      refreshNumbers();
    } catch (err) {
      toast(err.message, 'bad');
      if (b) b.classList.remove('picked');
    } finally {
      busy = false;
    }
  }

  function bind(viewEl) {
    const fieldEl = $('#hunt-field', viewEl);
    if (!fieldEl) return;
    fieldEl.addEventListener('pointerdown', (e) => {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      const r = fieldEl.getBoundingClientRect(), lay = layout();
      const shown = { x: e.clientX - r.left, y: e.clientY - r.top };
      if (!lay.phone) { pick(shown.x, shown.y, fieldEl, shown); return; }
      const b = target && lay.at(target);
      const at = b && Math.hypot(shown.x - b.x, shown.y - b.y) <= TAP_R ? target : lay.toField(shown.x, shown.y);
      pick(at.x, at.y, fieldEl, shown);
    });
    fieldEl.addEventListener('keydown', (e) => { // the banana is a button: Enter or Space picks it
      if ((e.key === 'Enter' || e.key === ' ') && e.target.id === 'hunt-banana' && target) {
        e.preventDefault();
        pick(target.x, target.y, fieldEl);
      }
    });
    if (target) throwTo(fieldEl, target); // the first banana of the visit comes in the same way
    else if (data && data.me && !data.me.done) { // the server had nothing down: ask for a banana
      api('/api/hunt/start', { method: 'POST', body: '{}' }).then((r) => { data.me = r.me; target = r.me.target; throwTo(fieldEl, target); }).catch(() => {});
    }
  }

  return { init, load, view, bind };
})();
