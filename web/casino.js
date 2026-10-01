/* What the Casino's live tables share (blackjack.js, poker.js): card faces, Onkey the dealer with his speech bubble
   and monkey chatter, the long-poll loop, countdowns and patching a region without losing focus. */
window.FiveCasino = (() => {
  'use strict';
  let esc = (s) => String(s), state = null, nameHtml = null, confetti = null;
  const SUIT = { s: '♠', h: '♥', d: '♦', c: '♣' };
  const SUIT_NAME = { s: 'spades', h: 'hearts', d: 'diamonds', c: 'clubs' };
  const RANK = { T: '10', J: 'J', Q: 'Q', K: 'K', A: 'A' };
  const RANK_NAME = { T: 'Ten', J: 'Jack', Q: 'Queen', K: 'King', A: 'Ace' };
  let muted = localStorage.getItem('fs.casinoMuted') === '1';
  let audio = null;
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  function init(ctx) { ({ esc, state, nameHtml, confetti } = ctx); }

  // ---- what players bought (Onkey's Shop) ---------------------------------------------------
  // Tables send the looks of everyone at them; they join the site-wide looks that nameHtml reads.
  function absorb(looks) {
    if (!looks || !state) return;
    state.looks = { ...(state.looks || {}), ...looks };
  }
  const wornBy = (name, slot) => (((state && state.looks) || {})[String(name || '').toLowerCase()] || { worn: {} }).worn[slot] || null;
  // The class an item adds to a player's cards, chips or seat ('' when they wear nothing there).
  const style = (name, slot) => (wornBy(name, slot) || {}).cls || '';
  // A player's name at the table: their name colour, badge and pranks; title on its own line when asked.
  const who = (name, opts = {}) => (nameHtml ? nameHtml(name, opts) : esc(name));
  // Just their title (bought, or a Title Swap prank), for a line of its own; '' when they have none.
  const title = (name) => who(name, { title: true }).slice(who(name).length);
  // Their Table win burst, out of their seat (any element with data-bettor), for everyone watching.
  function seatBurst(root, name) {
    const w = wornBy(name, 'table_win');
    if (!w || !confetti || !root) return;
    const seat = [...root.querySelectorAll('[data-bettor]')].find((el) => el.dataset.bettor.toLowerCase() === String(name).toLowerCase());
    if (seat) confetti(seat, false, { colors: w.colors, emoji: w.emoji });
  }

  // ---- dealing ---------------------------------------------------------------------------
  // The server settles a move at once; the page plays it out. Each card a page draws can carry a `key` (where it sits:
  // table, round, seat, hand, position) and a `seq` (its place in the real deal order). `deal(render)` renders twice:
  // a first pass collects the cards never seen before, sorts them by seq and gives each a start time DEAL_GAP after the
  // last (a face-down card turning over is a flip, FLIP_GAP); the second pass draws them, each flying in from Onkey at
  // its time. A card drawn again before it has landed keeps its place in the animation (a negative delay), so table
  // updates mid-deal don't skip it. `after()` holds results (totals, wins, the status line) until the last card lands.
  const DEAL_GAP = 380, FLIP_GAP = 520, DEAL_MS = 420;
  const seen = new Map();          // card key -> { start } (performance.now() time it starts moving)
  let planning = null, landAt = 0, quiet = true;
  const now = () => performance.now();
  function deal(render) {
    planning = [];
    render();
    const fresh = planning.filter((p) => !seen.has(p.key)).sort((a, b) => a.seq - b.seq);
    planning = null;
    let t = now() + 60;
    for (const p of fresh) {
      if (quiet || reduced()) { seen.set(p.key, { start: -1e9 }); continue; } // first sight of a table: no replay
      seen.set(p.key, { start: t, flip: p.flip });
      setTimeout(() => sound('card'), Math.max(0, t - now()));
      landAt = Math.max(landAt, t + (p.flip ? FLIP_GAP : DEAL_MS));
      t += p.flip ? FLIP_GAP : DEAL_GAP;
    }
    quiet = false;
    if (seen.size > 600) [...seen.keys()].slice(0, seen.size - 400).forEach((k) => seen.delete(k));
    return render();
  }
  // A new table (another tab, a sign-in): its cards on screen now are shown, not dealt.
  const dealReset = () => { quiet = true; };
  // Milliseconds until the last card dealt so far lands.
  const landing = () => Math.max(0, landAt - now());
  // Attributes for something that should wait for the cards: a class and its delay, or nothing once they've landed.
  const after = (cls = '') => {
    const ms = Math.round(landing());
    return ms > 0 ? ` class="${cls} after-deal" style="--after:${ms}ms"` : (cls ? ` class="${cls}"` : '');
  };
  function dealt(key, c, opts) {
    const none = { cls: '', style: '' };
    if (!key) return none;
    const k = `${key}:${c || 'back'}`;
    if (planning) {
      planning.push({ key: k, seq: opts.seq || 0, flip: !!c && seen.has(`${key}:back`) });
      return none;
    }
    const e = seen.get(k);
    if (!e || e.start < 0) return none;
    const delay = Math.round(e.start - now());
    if (delay < -(e.flip ? FLIP_GAP : DEAL_MS)) return none;
    return { cls: e.flip ? ' flip-in' : ' deal-in', style: ` style="--deal-delay:${delay}ms"` };
  }

  // ---- cards -------------------------------------------------------------------------
  // A face-up card ("As"), a face-down one (null), or an empty spot (''). opts: size, cls, and key / seq to deal it.
  function card(c, opts = {}) {
    const size = opts.size ? ` ${opts.size}` : '';
    const anim = c === '' ? { cls: '', style: '' } : dealt(opts.key, c, opts);
    const extra = (opts.cls ? ` ${opts.cls}` : '') + anim.cls;
    const style = anim.style;
    if (c === '') return `<span class="pcard empty${size}${extra}" aria-hidden="true"></span>`;
    if (!c) return `<span class="pcard back${size}${extra}"${style} role="img" aria-label="Face-down card"></span>`;
    const r = c[0], s = c[1];
    const red = s === 'h' || s === 'd';
    const label = `${RANK_NAME[r] || r} of ${SUIT_NAME[s]}`;
    return `<span class="pcard${red ? ' red' : ''}${size}${extra}"${style} role="img" aria-label="${label}">` +
      `<span class="pc-corner"><b>${RANK[r] || r}</b><i>${SUIT[s]}</i></span><span class="pc-pip" aria-hidden="true">${SUIT[s]}</span></span>`;
  }
  // A row of cards; with opts.key each is dealt as `${key}:${i}`, seq from opts.seq(i).
  const cards = (list, opts = {}) => (list || []).map((c, i) => card(c, { ...opts, key: opts.key ? `${opts.key}:${i}` : undefined,
    seq: opts.seq ? opts.seq(i) : 0 })).join('');

  // ---- Onkey the dealer ------------------------------------------------------------------
  // What Onkey says, by log event. {name} and {amount} are filled in; one line is picked at random.
  const QUIPS = {
    greet: ['Welcome to Onkey\'s table. No throwing bananas at the dealer.', 'Onkey deals. Onkey judges. Sit down.', 'Step right up. The house always has bananas.'],
    shuffle: ['New shoe. Onkey shuffles like a pro.', 'Shuffling. No peeking, you animals.'],
    deal: ['Cards coming out.', 'Fresh hands, fresh hopes.', 'Here we go. Good luck, you\'ll need it.'],
    blackjack: ['Blackjack! {name} gets paid 3 to 2.', 'Twenty-one, first try. Show-off.', '{name} hits blackjack. Onkey is impressed. Slightly.'],
    dealer_blackjack: ['Onkey has blackjack. Sorry, not sorry.', 'Ace and a face. Onkey wins.'],
    bust: ['Too many! {name} busts.', '{name} went bananas. Bust.', 'Over 21. Onkey saw that coming.'],
    dealer_bust: ['Onkey busts! Everybody still standing gets paid.', 'Onkey went over. Nobody saw that. Right?'],
    double: ['Doubling down? Bold monkey.', '{name} doubles. Onkey respects the confidence.'],
    split: ['Splitting? Two hands, twice the trouble.', '{name} splits. Onkey loves extra work.'],
    win: ['{name} wins {amount}. Onkey is mildly upset.', 'Pay the monkey! {name} takes {amount}.'],
    push: ['A push. Nobody\'s happy, nobody\'s sad.'],
    lose: ['Onkey takes {amount}. Thank you kindly.', 'The house thanks {name} for the {amount}.'],
    timeout: ['{name} fell asleep. Onkey moves on.', 'Tick tock, {name}. Too slow.'],
    sit: ['Pull up a stool, {name}.', 'Welcome, {name}. Leave your bananas at the door.'],
    leave: ['Bye, {name}. Come back with more credits.', '{name} walks away. Smart, or scared?'],
    hand: ['Shuffle up and deal.', 'Blinds are in. Let\'s go.', 'New hand. Onkey sees everything.'],
    start: ['Everyone\'s ready. Onkey deals!', 'Game on. May the best monkey win.'],
    stop: ['Not enough players. Onkey takes a break.'],
    allin: ['All in! {name} is feeling brave.', '{name} shoves! Somebody call the zoo.', 'All the chips. {name} has no fear.'],
    raise: ['{name} raises. Spicy.', 'A raise from {name}. Onkey smells a bluff.'],
    bet: ['{name} bets {amount}.', '{name} leads out.'],
    call: ['{name} calls.'],
    check: ['{name} checks.'],
    fold: ['{name} folds. Wise. Or cowardly.', '{name} lets it go.'],
    flop: ['Here comes the flop.', 'The flop. Onkey likes this one.'],
    turn: ['The turn.', 'Fourth card. Things are heating up.'],
    river: ['The river. Last chance.', 'River card. Read it and weep.'],
    pwin: ['{name} takes the pot of {amount}.', '{name} drags {amount}. Onkey keeps a banana for the house.', 'Ship it to {name}!'],
    chop: ['Chop it up! The pot is split.', 'A split pot. Share your bananas nicely.'],
    sit_out: ['{name} is napping. Sitting them out.'],
    bust_out: ['{name} is out of chips. Top up or go home.'],
    settings: ['New rules. Everyone ready up again.', 'Rules changed. Ready up, monkeys.'],
    ready: ['{name} is ready.', '{name} wants to play.'],
    topup: ['{name} reloads. Onkey approves.'],
  };
  // Lines that excite Onkey get more squeaks (eeks) than grunts (ooks).
  const EXCITED = new Set(['blackjack', 'dealer_bust', 'allin', 'pwin', 'chop', 'win', 'start', 'double', 'split', 'bust', 'entrance']);
  const pick = (list) => list[Math.floor(Math.random() * list.length)];
  function quip(kind, vars = {}) {
    const list = QUIPS[kind];
    if (!list) return '';
    return pick(list).replace(/\{(\w+)\}/g, (_, k) => (vars[k] != null ? vars[k] : ''));
  }
  function dealer(id, opts = {}) {
    return `<div class="onkey-dealer${opts.cls ? ' ' + opts.cls : ''}" id="${id}">` +
      `<img class="onkey-face" src="/assets/onkey-logo.png" alt="Onkey, the dealer" width="88" height="51">` +
      `<div class="onkey-bubble" role="status" aria-live="polite"><span class="ook" aria-hidden="true"></span><span class="say"></span></div></div>`;
  }
  // Onkey speaks: the bubble shows the monkey noises, then what they mean, while the chatter plays.
  const timers = new WeakMap();
  function say(el, kind, vars, line) {
    if (!el) return;
    const text = line || (typeof kind === 'string' && QUIPS[kind] ? quip(kind, vars) : String(kind || ''));
    if (!text) return;
    const bubble = el.querySelector('.onkey-bubble');
    const words = text.split(/\s+/).length;
    const n = Math.max(2, Math.min(6, Math.round(words / 2)));
    const excited = EXCITED.has(kind);
    const noises = Array.from({ length: n }, (_, i) => (excited ? (i % 3 === 2 ? 'ook' : 'eek') : (i % 3 === 1 ? 'eek' : 'ook')));
    bubble.querySelector('.ook').textContent = noises.map((x) => x[0].toUpperCase() + x.slice(1) + (excited ? '!' : '')).join(' ');
    bubble.querySelector('.say').textContent = text;
    el.classList.remove('talking');
    void el.offsetWidth; // restart the animation
    el.classList.add('talking');
    chatter(noises);
    clearTimeout(timers.get(el));
    timers.set(el, setTimeout(() => el.classList.remove('talking'), 2400 + words * 160));
  }
  // The log entries since the last one seen, as one line from Onkey (the most interesting one).
  const PRIORITY = ['dealer_blackjack', 'blackjack', 'allin', 'pwin', 'chop', 'dealer_bust', 'start', 'settings', 'bust_out',
    'sit_out', 'timeout', 'double', 'split', 'bust', 'win', 'raise', 'shuffle', 'hand', 'deal', 'river', 'turn', 'flop',
    'stop', 'sit', 'leave', 'topup', 'ready', 'bet', 'lose', 'push', 'fold', 'call', 'check'];
  function react(el, entries, kindOf = (e) => e.kind, varsOf = (e) => ({ name: e.bettor, amount: e.amount })) {
    let best = null, rank = Infinity;
    for (const e of entries) {
      const k = kindOf(e);
      const r = PRIORITY.indexOf(k);
      if (r >= 0 && r < rank) { best = e; rank = r; }
    }
    if (!best) return;
    // Someone who bought an Entrance is announced with their own line.
    const entrance = best.kind === 'sit' && best.bettor && wornBy(best.bettor, 'entrance');
    if (entrance) say(el, 'entrance', {}, entrance.text.replace(/\{name\}/g, best.bettor));
    else say(el, kindOf(best), varsOf(best));
  }

  // ---- monkey noises (Web Audio, nothing to download) ---------------------------------------------
  function ctx() {
    if (muted) return null;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      if (audio.state === 'suspended') audio.resume();
      return audio;
    } catch (e) { return null; }
  }
  // One syllable: "ook" is a low grunt that falls, "eek" a squeak that rises. A sawtooth through a band-pass filter
  // gives it a throaty, vowel-ish sound.
  function syllable(a, kind, at) {
    const o = a.createOscillator(), f = a.createBiquadFilter(), g = a.createGain();
    const low = kind === 'ook';
    const base = (low ? 190 : 760) * (0.9 + Math.random() * 0.25);
    o.type = low ? 'sawtooth' : 'square';
    o.frequency.setValueAtTime(base, at);
    o.frequency.exponentialRampToValueAtTime(low ? base * 0.7 : base * 1.6, at + (low ? 0.13 : 0.09));
    f.type = 'bandpass';
    f.frequency.setValueAtTime(low ? 650 : 1900, at);
    f.Q.value = low ? 5 : 8;
    g.gain.setValueAtTime(0.0001, at);
    g.gain.exponentialRampToValueAtTime(low ? 0.22 : 0.12, at + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, at + (low ? 0.16 : 0.11));
    o.connect(f); f.connect(g); g.connect(a.destination);
    o.start(at); o.stop(at + 0.2);
  }
  function chatter(noises) {
    const a = ctx();
    if (!a) return;
    let t = a.currentTime + 0.02;
    for (const n of noises) { syllable(a, n, t); t += n === 'ook' ? 0.17 : 0.12; }
  }
  // Small table sounds: a card flick, chips, a win.
  function sound(kind) {
    const a = ctx();
    if (!a) return;
    const t = a.currentTime + 0.01;
    if (kind === 'card') {
      const len = 0.05, buf = a.createBuffer(1, a.sampleRate * len, a.sampleRate), d = buf.getChannelData(0);
      for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length) ** 3;
      const src = a.createBufferSource(), f = a.createBiquadFilter(), g = a.createGain();
      src.buffer = buf; f.type = 'highpass'; f.frequency.value = 1800; g.gain.value = 0.35;
      src.connect(f); f.connect(g); g.connect(a.destination); src.start(t);
    } else if (kind === 'chips') {
      for (let i = 0; i < 3; i++) {
        const o = a.createOscillator(), g = a.createGain();
        o.type = 'triangle'; o.frequency.value = 2400 + Math.random() * 900;
        g.gain.setValueAtTime(0.08, t + i * 0.045); g.gain.exponentialRampToValueAtTime(0.0001, t + i * 0.045 + 0.06);
        o.connect(g); g.connect(a.destination); o.start(t + i * 0.045); o.stop(t + i * 0.045 + 0.08);
      }
    } else if (kind === 'win') {
      [523, 659, 784, 1047].forEach((hz, i) => {
        const o = a.createOscillator(), g = a.createGain();
        o.type = 'triangle'; o.frequency.value = hz;
        g.gain.setValueAtTime(0.0001, t + i * 0.09); g.gain.exponentialRampToValueAtTime(0.14, t + i * 0.09 + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + i * 0.09 + 0.3);
        o.connect(g); g.connect(a.destination); o.start(t + i * 0.09); o.stop(t + i * 0.09 + 0.35);
      });
    }
  }
  const speaker = () => `<button type="button" class="btn ghost icon casino-mute" aria-pressed="${!muted}" aria-label="${muted ? 'Turn sound on' : 'Turn sound off'}" title="${muted ? 'Sound off' : 'Sound on'}">${muted ? '🔇' : '🔊'}</button>`;
  function bindSpeaker(root) {
    root.querySelectorAll('.casino-mute').forEach((b) => b.addEventListener('click', () => {
      muted = !muted;
      try { localStorage.setItem('fs.casinoMuted', muted ? '1' : '0'); } catch (e) { /* unavailable */ }
      b.outerHTML = speaker();
      bindSpeaker(root);
      if (!muted) chatter(['ook', 'eek']);
    }));
  }

  // ---- live pages -------------------------------------------------------------------------------
  // Replace a region's HTML only when it changed, keeping keyboard focus (and a text field's caret) where it was.
  function patch(el, html) {
    if (!el || el._html === html) return false;
    const active = document.activeElement;
    const keep = active && el.contains(active) && active.id ? { id: active.id, start: active.selectionStart, end: active.selectionEnd } : null;
    el.innerHTML = html;
    el._html = html;
    if (keep) {
      const again = document.getElementById(keep.id);
      if (again) {
        again.focus();
        try { if (keep.start != null) again.setSelectionRange(keep.start, keep.end); } catch (e) { /* not a text field */ }
      }
    }
    return true;
  }
  // A long-poll loop: `fetchNext(version)` waits on the server for a change; `apply(data)` takes it. It stops when
  // `alive()` turns false (the page was left) and backs off for a few seconds after an error.
  function live({ alive, fetchNext, apply, version }) {
    let running = true;
    (async () => {
      while (running && alive()) {
        try {
          const d = await fetchNext(version());
          if (!running || !alive()) break;
          apply(d);
        } catch (e) {
          await new Promise((r) => setTimeout(r, 3000));
        }
      }
      running = false;
    })();
    return { stop: () => { running = false; }, get running() { return running; } };
  }
  // Countdowns: any element with data-deadline (server time, seconds) shows the seconds left; a .timer-bar with
  // data-deadline and data-span shrinks. `offset` is the server's clock minus ours.
  let offset = 0;
  const syncClock = (serverTime) => { if (serverTime) offset = serverTime - Date.now() / 1000; };
  const secondsLeft = (deadline) => Math.max(0, deadline - (Date.now() / 1000 + offset));
  setInterval(() => {
    document.querySelectorAll('[data-deadline]').forEach((el) => {
      const left = secondsLeft(+el.dataset.deadline);
      if (el.classList.contains('timer-bar')) el.style.setProperty('--left', String(Math.min(1, left / (+el.dataset.span || 30))));
      else el.textContent = Math.ceil(left) + 's';
      el.classList.toggle('urgent', left < 6);
    });
  }, 250);
  const timer = (deadline, span) => (deadline ? `<span class="timer-bar" data-deadline="${deadline}" data-span="${span}" style="--left:${Math.min(1, secondsLeft(deadline) / span)}"></span>` : '');
  const countdown = (deadline) => (deadline ? `<b class="countdown" data-deadline="${deadline}">${Math.ceil(secondsLeft(deadline))}s</b>` : '');
  const ref = () => (crypto.randomUUID ? crypto.randomUUID() : `r${Date.now()}${Math.random().toString(36).slice(2)}`).replace(/[^A-Za-z0-9-]/g, '').padEnd(16, '0');

  return { init, card, cards, dealer, say, react, quip, sound, speaker, bindSpeaker, patch, live, syncClock, secondsLeft, timer, countdown, ref, reduced,
    absorb, wornBy, style, who, title, seatBurst, deal, dealReset, landing, after };
})();
