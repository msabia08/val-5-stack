/* Server-settled slots. Reel motion is cosmetic; only the server chooses the result. */
window.FiveSlots = (() => {
  'use strict';
  let state, $, $$, api, draw, esc, fmt, loadMe;
  let data = null, owner = null, stake = 10, busy = false, result = null, error = '', pending = null;
  let motion = null, audio = null, muted = localStorage.getItem('fs.slotsMuted') === '1';
  const storageKey = (name) => `fs.slotSpin.${name.toLowerCase()}`;
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  function init(ctx) { ({ state, $, $$, api, draw, esc, fmt, loadMe } = ctx); }
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
  const icon = (i) => `<span role="img" aria-label="${esc(data.symbols[i].name)}">${data.symbols[i].icon}</span>`;
  const reelsNow = () => result?.reels || [0, 3, 5];
  const wrap = (n) => ((n % 6) + 6) % 6;
  // Three copies make the wrap invisible: the visible window always sits in the middle copy.
  const reel = (value) => `<div class="slots-reel" role="img" aria-label="${esc(data.symbols[value].name)}"><div class="slots-strip" aria-hidden="true" style="transform:translate3d(0,${70 - (6 + value) * 140}px,0)">${Array.from({ length: 18 }, (_, i) => `<span class="slots-symbol">${data.symbols[i % 6].icon}</span>`).join('')}</div></div>`;

  function sound(kind, index = 0) {
    if (muted || document.hidden || state.view !== 'slots') return;
    try {
      audio ||= new (window.AudioContext || window.webkitAudioContext)();
      audio.resume().catch(() => {});
      const notes = kind === 'win' ? [523.25, 659.25, 783.99, 1046.5] : [kind === 'start' ? 220 : 330 + index * 110];
      notes.forEach((frequency, i) => {
        const oscillator = audio.createOscillator(), gain = audio.createGain(), at = audio.currentTime + i * .09;
        oscillator.type = 'sine'; oscillator.frequency.value = frequency;
        gain.gain.setValueAtTime(0, at); gain.gain.linearRampToValueAtTime(.055, at + .008);
        gain.gain.exponentialRampToValueAtTime(.001, at + .18);
        oscillator.connect(gain).connect(audio.destination); oscillator.start(at); oscillator.stop(at + .2);
        oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
      });
    } catch (_) { /* sound is optional */ }
  }

  function startMotion() {
    const start = performance.now(), positions = [...reelsNow()], brakes = [], stopped = [false, false, false];
    let previous = start, frame = 0, targets = null, stopAt = Infinity, ended = false, resolve;
    const done = new Promise((r) => { resolve = r; });
    const finish = () => { ended = true; cancelAnimationFrame(frame); resolve(); };
    function paint() {
      $$('.slots-strip').forEach((strip, i) => {
        strip.style.transform = `translate3d(0,${70 - (6 + wrap(positions[i])) * 140}px,0)`;
        strip.parentElement.classList.toggle('stopped', stopped[i]);
        if (stopped[i]) strip.parentElement.setAttribute('aria-label', data.symbols[targets[i]].name);
      });
    }
    function tick(now) {
      if (ended) return;
      if (state.view !== 'slots' || reduced()) { finish(); return; }
      const dt = Math.min((now - previous) / 1000, .05); previous = now;
      positions.forEach((position, i) => {
        if (stopped[i]) return;
        const velocity = (14 + i) * Math.min((now - start) / 420, 1);
        if (targets && now >= stopAt + i * 180 && !brakes[i]) {
          const duration = 1100 + i * 160;
          const from = position + velocity * dt;
          const travel = velocity * duration / 2000;
          const distance = travel + wrap(targets[i] - from - travel);
          brakes[i] = { at: now, from, distance, duration, tangent: velocity * duration / 1000 };
        }
        const b = brakes[i];
        if (!b) { positions[i] += velocity * dt; return; }
        const t = Math.min((now - b.at) / b.duration, 1);
        // Cubic Hermite: preserve incoming velocity and arrive exactly at the result with zero velocity.
        positions[i] = b.from + b.tangent * (t * t * t - 2 * t * t + t) + b.distance * (-2 * t * t * t + 3 * t * t);
        if (t === 1) { stopped[i] = true; sound('stop', i); }
      });
      paint();
      if (stopped.every(Boolean)) finish();
      else frame = requestAnimationFrame(tick);
    }
    if (!reduced()) frame = requestAnimationFrame(tick);
    else finish();
    return { paint, cancel: finish, settle: (reels) => { targets = reels; stopAt = Math.max(performance.now(), start + 800); return done; } };
  }

  function history() {
    return `<section class="card slots-history"><div class="section-head"><h2>Your recent spins</h2>${data.me ? `<span class="muted small">This season: ${data.me.spins} spins · ${signed(data.me.net)} credits net</span>` : ''}</div>
      ${data.history.length ? `<div class="table-wrap"><table><thead><tr><th>Reels</th><th>Time</th><th class="num">Stake</th><th class="num">Returned</th><th class="num">Net</th></tr></thead><tbody>${data.history.map((s) => `<tr><th scope="row" class="slots-history-reels">${s.reels.map(icon).join('')}</th><td class="muted">${esc(new Date(s.created_ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }))}</td><td class="num">${money(s.stake)}</td><td class="num">${money(s.payout)}</td><td class="num ${s.net > 0 ? 'up' : s.net < 0 ? 'down' : ''}">${signed(s.net)}</td></tr>`).join('')}</tbody></table></div>` : `<p class="muted">${state.me ? 'Your first spin will appear here.' : 'Sign in to play and see your spin history.'}</p>`}</section>`;
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
    const win = !busy && result?.net > 0;
    const message = result ? result.payout ? `${money(result.payout)} credits returned` : 'No winning line' : 'Ready when you are';
    const detail = result ? `${signed(result.net)} credits net` : 'Three matching symbols on the centre line pays.';
    return `<div class="slots-heading"><div><h1>Slots</h1><p>Three reels. One winning line.</p></div><a class="go-link" href="#arcade">Onkey’s Arcade ›</a></div>
      <div class="slots-layout"><section class="slots-cabinet ${win ? 'slots-win' : ''}">
        <div class="slots-marquee"><img src="/assets/onkey-logo.png" alt="" width="83" height="48"><h2>Slots</h2><button class="slots-sound" id="slot-sound" aria-pressed="${!muted}">${muted ? 'Sound off' : 'Sound on'}</button></div>
        <div class="slots-stage"><div class="slots-glass"><div class="slots-reels ${busy ? 'spinning' : ''}" aria-label="${busy ? 'Reels spinning' : 'Reel result'}" aria-busy="${busy}">${reelsNow().map(reel).join('')}</div><div class="slots-payline" aria-hidden="true"></div><span class="slots-line-arrow left" aria-hidden="true">▸</span><span class="slots-line-arrow right" aria-hidden="true">◂</span></div>
          <div class="slots-result" role="status" aria-live="polite"><b>${busy ? 'Spinning…' : esc(message)}</b><span>${busy ? 'Let them roll.' : esc(detail)}</span></div>
        </div>
        <div class="slots-deck"><div class="slots-controls"><span id="slot-stake-label">Credits per spin</span><div class="slots-stakes" role="group" aria-labelledby="slot-stake-label">${data.stakes.map((s) => `<button type="button" data-slot-stake="${s}" aria-pressed="${s === stake}" ${locked ? 'disabled' : ''}>${s}</button>`).join('')}</div></div>
          ${state.me ? `<button class="slots-spin" id="slot-spin" ${busy || (insufficient && !pending) ? 'disabled' : ''}><span>${busy ? 'Spinning…' : pending ? 'Check last spin' : 'Spin'}</span><small>${pending && !busy ? 'Recover your result' : `${stake} credits`}</small></button>` : '<button class="slots-spin" data-signin><span>Sign in to spin</span><small>Play with betting credits</small></button>'}
        </div><div class="slots-foot"><span>Virtual credits only</span><span>Match 3 to win</span></div>
        <p class="slots-error" role="alert">${esc(error || (insufficient && !pending ? 'Not enough credits. Choose a smaller stake.' : ''))}</p>
      </section><aside class="card slots-paytable"><h2>The payouts</h2><p class="muted small">Match three on the centre line.<br>Payouts include your stake.</p>
        <div class="slots-prizes">${[5, 4, 3, 2, 1, 0].map((i) => `<div class="slots-prize ${win && result.reels.every((r) => r === i) ? 'won' : ''}"><span class="slots-pay-symbol" aria-hidden="true">${data.symbols[i].icon.repeat(3)}</span><span class="sr-only">Three ${esc(data.symbols[i].name)}</span><span><b>${m.triples[i]}×</b><small>${money(m.triples[i] * stake)} credits</small></span></div>`).join('')}</div>
        <details class="how"><summary>Every spin is independent.</summary><p>Each reel picks one of six equally likely symbols. Three of a kind pays; pairs and mixed symbols pay 0. Average return: ${m.rtp}% over many spins. Results settle immediately and cannot be cancelled.</p><p>Slot results have their own season totals. Match-betting ROI stays separate. Slots do not earn shop bananas.</p></details>
      </aside></div>${history()}`;
  }
  async function spin() {
    if (busy || !state.me) return;
    if (!pending) {
      const bytes = new Uint8Array(16); crypto.getRandomValues(bytes);
      remember({ stake, request_id: Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('') });
    }
    const name = owner, request = pending;
    busy = true; error = ''; sound('start'); draw();
    const rolling = motion = startMotion();
    try {
      const out = await api('/api/slots/spin', { method: 'POST', body: JSON.stringify(request) });
      await rolling.settle(out.spin.reels);
      if (owner !== name) return;
      result = out.spin; remember(null);
      if (state.me?.name === name) state.me.balance = out.balance;
      if (result.net > 0) sound('win');
      await Promise.all([load(), loadMe()]);
    } catch (e) {
      if (owner === name) {
        if (e.status >= 400 && e.status < 500) remember(null);
        error = pending ? 'The result could not be confirmed. Check last spin to recover it without paying twice.' : e.message;
      }
    } finally {
      rolling.cancel(); motion = null; busy = false;
      if (state.view === 'slots') { draw(); $('#slot-spin')?.focus(); }
    }
  }
  function bind(viewEl) {
    $$('[data-slot-stake]', viewEl).forEach((button) => button.addEventListener('click', () => {
      stake = Number(button.dataset.slotStake); error = ''; draw();
      $(`[data-slot-stake="${stake}"]`)?.focus();
    }));
    $('#slot-spin', viewEl)?.addEventListener('click', spin);
    $('#slot-sound', viewEl)?.addEventListener('click', (e) => {
      muted = !muted; localStorage.setItem('fs.slotsMuted', muted ? '1' : '0');
      e.currentTarget.textContent = muted ? 'Sound off' : 'Sound on'; e.currentTarget.setAttribute('aria-pressed', String(!muted));
    });
    if (state.view === 'slots') motion?.paint();
  }
  return { init, load, view, bind };
})();
